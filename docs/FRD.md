# 📋 Functional Requirements Document (FRD)

## Project: Geospatial File Measurement & Intelligence API
**Target Platform:** Aereo Cloud (Drone Vector Ingestion & Telemetry Processing Engine)  
**Author:** Sanskar Sinha  
**Version:** 2.0.0 (Enterprise Specification)  
**Status:** Approved & Implemented  

---

## 1. Executive Summary & Objective

The **Geospatial File Measurement API** is an enterprise-grade backend service built to ingest geospatial vector files (ESRI Shapefiles, OpenGIS KML, and compressed KMZ archives), extract geometric features, reproject geographic coordinates into local metric coordinate systems, calculate high-precision physical measurements (area and length), and cross-validate them against the WGS-84 ellipsoid.

This document formally captures the **functional capabilities, system requirements, data contracts, and algorithmic constraints** governing the production implementation.

---

## 2. Stakeholders & Personas

| Persona | Description | Primary Needs |
| :--- | :--- | :--- |
| **Drone Survey Operations** | Field pilots uploading orthomosaic boundary files and drone mission footprints. | Rapid ingestion, instant validation, feedback on corrupt geometry. |
| **Mine / Infrastructure Planners** | Engineers calculating parcel areas, haul road distances, and cut-fill boundary footprints. | High-precision metric measurements ($m^2$, hectares, $km$), low distortion. |
| **Enterprise Frontend (Aereo Cloud)** | Web & GIS dashboards visualizing drone telemetry. | Standard RFC 7946 GeoJSON, WGS-84 overlay layers, streaming CSV exports. |
| **Platform / DevOps Engineers** | Engineering team maintaining uptime, CI/CD, and scalability. | Low memory footprint, zero GDAL system hell, Docker readiness, 97%+ test coverage. |

---

## 3. Scope of System

### 3.1 In Scope
- Single-pass streaming ingestion of Shapefiles (`.zip`), KML (`.kml`), and KMZ (`.kmz`).
- Automated source CRS parsing from `.prj` or XML headers; intelligent fallback for unprojected coordinate sets.
- Centroid-driven dynamic projection to local **Universal Transverse Mercator (UTM)** metric coordinate systems (EPSG:326XX / EPSG:327XX) and **Universal Polar Stereographic (UPS)** near polar regions ($|\phi| \ge 84^\circ$).
- Dual-computation engine:
  - **Planar Metric Measurements**: Area ($m^2$, $ha$, $km^2$) and Perimeter ($m$) for Polygons; Length ($m$, $km$) for LineStrings.
  - **Geodesic Ellipsoidal Ground Truth**: Cross-validated with `pyproj.Geod` over the WGS-84 ellipsoid.
  - **Distortion Percentage ($\Delta\%$)**: Explicitly reported per feature to surface projection deformations.
- Zero-crash per-feature fault isolation: Malformed geometry in feature $N$ does not abort features $1 \dots N-1$.
- Idempotent upload handling via cryptographic **SHA-256 deduplication**.
- Multi-format streaming data exports: RFC 7946 GeoJSON FeatureCollections and CSV with formula injection defense.
- Interactive Dark-Mode Web Dashboard (`/`) for drone survey visualization.

### 3.2 Out of Scope (Scheduled for Future Releases)
- Direct 3D volumetric calculations on raster DSM/DTM (GeoTIFF) rasters.
- Spatial joins, PostGIS spatial indexing, and topological editing.
- User authentication and role-based multi-tenancy (architecturally planned in database schema).

---

## 4. Functional Requirements

### 4.1 Ingestion & Security (FR-01)
- **FR-01.1 (File Formats)**: System MUST accept `.zip` (Shapefile archives), `.kml` (XML 2.2), and `.kmz` (ZIP containing `.kml`).
- **FR-01.2 (Security Validation)**:
  - Deep magic bytes inspection (`PK\x03\x04` for ZIP/KMZ, XML headers for KML).
  - Enforce maximum upload limit: $50\text{ MB}$ (configurable via `MAX_UPLOAD_SIZE_MB`).
  - Enforce zip-bomb defense: maximum uncompressed expansion of $250\text{ MB}$ and max 500 entries.
  - Prevent Zip-Slip directory traversal vulnerabilities by reading archives completely in memory.
  - Mitigate XML External Entity (XXE) and billion-laughs attacks using `defusedxml`.
- **FR-01.3 (Deduplication)**: Calculate SHA-256 hash in a single streaming pass. If an identical file exists, return HTTP 200 with the existing record unless disabled.

### 4.2 Geospatial Parsing & Feature Extraction (FR-02)
- **FR-02.1 (Shapefile Ingestion)**: Extract geometry from `.shp`, spatial index from `.shx`, attributes from `.dbf`, and projection definition from `.prj`.
- **FR-02.2 (Missing Projection Protocol)**:
  - If `.prj` is missing and coordinates fall within $[-180, 180] \times [-90, 90]$, assume `EPSG:4326` and record a warning.
  - If `.prj` is missing and coordinates exceed geographic bounds ($> 180^\circ$), reject with `422 Unprocessable Entity (CRS_MISSING)`.
- **FR-02.3 (KML Ingestion)**: Support `Placemark` structures containing `Polygon` (including inner exclusion holes), `LineString`, `Point`, and `MultiGeometry`.
- **FR-02.4 (Graceful Fault Isolation)**:
  - Features with invalid or degenerate geometry MUST be repaired via `shapely.make_valid()`.
  - If unrepairable, the feature MUST be flagged with status `INVALID_GEOMETRY`, leaving the remaining features intact.
  - Point features MUST be safely accepted with measurement status `NONE`.

### 4.3 Coordinate Reference System (CRS) & Projection Engine (FR-03)
- **FR-03.1 (No Degree Calculations)**: The system MUST NEVER compute Euclidean distance or area directly on geographic coordinates (degrees).
- **FR-03.2 (Dynamic Local Metric Projection)**:
  - For each feature, determine centroid longitude $\lambda$ and latitude $\phi$.
  - Calculate UTM Zone: $\text{Zone} = \lfloor(\lambda + 180)/6\rfloor + 1$.
  - Assign EPSG code: $\text{EPSG:32600} + \text{Zone}$ for Northern Hemisphere ($\phi \ge 0$), $\text{EPSG:32700} + \text{Zone}$ for Southern Hemisphere ($\phi < 0$).
  - For polar coordinates ($|\phi| \ge 84^\circ$), project to UPS North (`EPSG:32661`) or UPS South (`EPSG:32761`).
- **FR-03.3 (Dual Geometric Computation)**:
  - Compute planar measurements on the projected metric geometry ($m^2$, $m$).
  - Compute ellipsoidal measurements using the WGS-84 reference ellipsoid ($a = 6378137.0\text{ m}, 1/f = 298.257223563$).
  - Calculate distortion percentage: $\Delta\% = |\text{Planar} - \text{Geodesic}| / \text{Geodesic} \times 100$.
  - Flag features where distortion exceeds $0.5\%$ with an explanatory note.

### 4.4 Data Persistence & Data Model (FR-04)
- **FR-04.1 (Storage)**: Persist all datasets using SQLAlchemy Async ORM with portable SQL types (compatible with SQLite and PostgreSQL).
- **FR-04.2 (Entity Relationships)**:
  - `GeospatialFile`: Tracks file metadata, SHA-256 hash, CRS, total feature count, processing status, and aggregated telemetry summary.
  - `GeospatialFeature`: Child entity storing feature sequence, geometry as GeoJSON, map-ready WGS-84 GeoJSON, native properties, status, notes, and measurement breakdown.

### 4.5 API Endpoints & Export Contracts (FR-05)
- **`POST /api/files/`**: Accepts multipart file upload. Returns HTTP 201 (new file), HTTP 200 (deduplicated), or HTTP 202 (`?background=true`).
- **`GET /api/files/{id}/`**: Returns file metadata, progress percentage, and warnings.
- **`GET /api/files/{id}/measurements/`**: Returns file summary and paginated feature measurements (`limit`, `offset`, `geometry_type` filters).
- **`GET /api/files/{id}/export?format={geojson|csv}`**:
  - Streamed RFC 7946 GeoJSON FeatureCollection.
  - Streamed CSV report with OWASP CSV Injection sanitization (prepends `'` to leading `=`, `+`, `-`, `@`).
- **`GET /api/files/`**: Lists all uploaded files with pagination and `X-Total-Count` header.
- **`DELETE /api/files/{id}/`**: Deletes file record, child features, and stored upload artifact.
- **`GET /health`**: Verifies service liveness and database connectivity.

---

## 5. Non-Functional Requirements (NFR)

1. **Performance**: Process 1,000 vector features in $< 500\text{ ms}$.
2. **Memory Efficiency**: In-memory streaming uploads prevent unbounded RAM consumption.
3. **Zero Native C-Dependencies Hassle**: Pure wheels (`shapely 2.x`, `pyproj`, `pyshp`) eliminate host-level GDAL compilation.
4. **Reliability & Test Coverage**: Maintain $\ge 95\%$ automated line coverage (currently $97\%$, 189 passing tests).
5. **Observability**: Structured JSON logging, client request IDs, and uniform error payload schemas (`detail`, `error_code`, `file_id`).

---

## 6. Verification & Acceptance Criteria

| Criteria | Verification Method | Pass Condition |
| :--- | :--- | :--- |
| **CRS Degree Prohibition** | Static Analysis & Unit Tests | No direct Shapely area/length computations executed in degree CRS. |
| **Global Mining Coverage** | Integration Test (`TestGlobalCoverage`) | 6 global benchmark sites (Bengaluru, Dubai, Johannesburg, Pilbara, Chuquicamata, Bergen) project accurately with distortion $< 0.4\%$. |
| **Security Resilience** | Automated Security Tests | Zip bombs, XXE strings, and CSV injection payloads handled cleanly. |
| **Error Isolation** | Malformed Feature Tests | Bad feature does not abort valid sibling features. |
| **Test Suite Health** | CI Execution | 189 tests passing, Ruff clean. |
