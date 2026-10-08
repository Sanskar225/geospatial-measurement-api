# 🛰️ Geospatial File Measurement API

[![Python Version](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue.svg)](https://www.python.org/)
[![Framework](https://img.shields.io/badge/Framework-FastAPI-009688.svg)](https://fastapi.tiangolo.com/)
[![Tests](https://img.shields.io/badge/Tests-18%2F18%20Passed-brightgreen.svg)]()
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED.svg)](https://www.docker.com/)
[![License](https://img.shields.io/badge/License-MIT-purple.svg)]()

A high-performance, enterprise-grade backend service built for **geospatial feature extraction, dynamic CRS metric projection, and geometric measurement calculation**. Engineered specifically to process drone mapping datasets, cadastral surveys, and GIS vector boundaries formatted as **ESRI Shapefiles (`.zip`)** or **Keyhole Markup Language (`.kml`)**.

---

## 📑 Table of Contents
- [Key Features](#-key-features)
- [Architecture & System Flow](#-architecture--system-flow)
- [CRS & Projection Engineering](#-crs--projection-engineering)
- [Project Structure](#-project-structure)
- [Quickstart & Setup](#-quickstart--setup)
- [API Reference & Examples](#-api-reference--examples)
- [Design Decisions & Alternatives](#-design-decisions--alternatives)
- [Learnings & Future Scope](#-learnings--future-scope)

---

## 🌟 Key Features

1. **Multi-Format Vector Ingestion**:
   - ESRI Shapefile Archives (`.zip` containing `.shp`, `.shx`, `.dbf`, `.prj`).
   - OpenGIS KML 2.2 (`.kml`) with support for Polygons (including interior exclusion rings/holes), LineStrings, Points, and MultiGeometries.
2. **Mathematically Accurate CRS Handling**:
   - Never calculates distances or areas in raw geographic degrees ($^{\circ}$).
   - Dynamically calculates the centroid of geometries and automatically projects them into the optimal **Universal Transverse Mercator (UTM)** metric coordinate system.
3. **Resilient Measurement Engine**:
   - **Polygon / MultiPolygon**: Area in square meters ($m^2$), hectares, and $km^2$.
   - **LineString / MultiLineString**: Length in meters ($m$) and kilometers ($km$).
   - **Points**: Handled gracefully without crash or invalid calculations.
   - **Invalid Geometry Healing**: Auto-repairs self-intersecting geometries via Shapely's `make_valid`.
4. **Interactive Geospatial Dashboard**:
   - Includes a built-in dark-mode Leaflet web viewer (`http://localhost:8000`) with drag-and-drop file upload, real-time map polygon rendering, and interactive metric popups.
5. **Security & Production Hardening**:
   - Zip-slip directory traversal protection.
   - Zip-bomb decompression threshold safeguards.
   - XML External Entity (XXE) attack prevention via `defusedxml`.

---

## 🏗️ Architecture & System Flow

```mermaid
flowchart TD
    Client(["Client / Browser"]) -->|POST /api/files/ (.zip / .kml)| UploadRouter["Upload Router (endpoints.py)"]
    
    subgraph Ingestion ["1. Secure Ingestion & Parsing"]
        UploadRouter -->|Validate extension & size| FileRouter{File Extension}
        FileRouter -->|".zip"| ShpParser["ShapefileParser (pyshp)"]
        FileRouter -->|".kml"| KmlParser["KMLParser (defusedxml)"]
        ShpParser -->|Extract .shp, .dbf, .prj| ExtractedFeatures["Standardized Feature Objects"]
        KmlParser -->|Parse Placemarks & ExtendedData| ExtractedFeatures
    end

    subgraph CRS_Engine ["2. CRS & Coordinate Transformation"]
        ExtractedFeatures --> CRSService["CRSService (pyproj)"]
        CRSService --> CheckCRS{"Is CRS Geographic?"}
        CheckCRS -->|Yes (e.g. EPSG:4326)| AutoUTM["Compute Centroid Lon/Lat -> Determine Optimal UTM EPSG (326XX / 327XX)"]
        CheckCRS -->|No (Already Projected)| DirectPlanar["Retain Native Projected CRS"]
        AutoUTM --> Transform["Reproject to Metric Planar System (meters)"]
        DirectPlanar --> Transform
    end

    subgraph Measurements ["3. Measurement Calculation"]
        Transform --> MeasService["MeasurementService (shapely 2.x)"]
        MeasService --> GeomType{"Geometry Type"}
        GeomType -->|"Polygon / MultiPolygon"| CalcArea["Calculate Planar Area (m², ha, km²)"]
        GeomType -->|"LineString / MultiLineString"| CalcLength["Calculate Planar Length (m, km)"]
        GeomType -->|"Point / MultiPoint"| SkipPoint["Skip Measurement (Point = 0-dim)"]
        GeomType -->|"Unsupported / Malformed"| GracefulHandle["Flag UNSUPPORTED_GEOMETRY"]
    end

    subgraph Persistence ["4. Caching & Response"]
        CalcArea --> Storage["StorageService (In-Memory + JSON Store)"]
        CalcLength --> Storage
        SkipPoint --> Storage
        GracefulHandle --> Storage
        Storage --> Response["JSON Response (FileInfo & Measurements)"]
    end
```

---

## 🌐 CRS & Projection Engineering

### Why Direct Lat/Long Calculation is Flawed
Geographic Coordinate Systems (like **WGS 84 / EPSG:4326**) express positions in angular units (degrees of latitude and longitude) on a three-dimensional reference ellipsoid:
- One degree of longitude is approximately $111.32\text{ km}$ at the equator, but shrinks to $0\text{ km}$ at the poles:
  $$\Delta x \approx 111.32 \cdot \cos(\text{latitude}) \text{ km}$$
- Calculating Euclidean distance ($\sqrt{\Delta x^2 + \Delta y^2}$) or Shoelace polygon area directly on degrees yields meaningless, geographically distorted numbers.

### Our Dynamic Projection Strategy
1. **Centroid Extraction**: Calculate the representative center coordinate $(\lambda, \phi)$ in degrees.
2. **Optimal UTM Zone Detection**: Universal Transverse Mercator divides the Earth into 60 longitudinal zones ($6^{\circ}$ wide):
   $$\text{Zone} = \left\lfloor \frac{\lambda + 180^{\circ}}{6} \right\rfloor + 1$$
   - Northern Hemisphere ($\phi \ge 0$): $\text{EPSG} = 32600 + \text{Zone}$
   - Southern Hemisphere ($\phi < 0$): $\text{EPSG} = 32700 + \text{Zone}$
3. **Planar Transformation**: Using PROJ's coordinate pipeline, coordinates are converted into orthogonal meter coordinates $(x, y)$, yielding accurate Cartesian area and length measurements.

---

## 📂 Project Structure

```
geospatial-measurement-api/
│
├── app/
│   ├── __init__.py
│   ├── main.py                     # FastAPI application factory, CORS, static UI mounts
│   ├── core/
│   │   ├── config.py               # Application settings & constraints
│   │   └── exceptions.py           # Custom exception definitions & status codes
│   ├── models/
│   │   └── schemas.py              # Pydantic v2 schemas for request/response serialization
│   ├── services/
│   │   ├── crs_service.py          # Dynamic UTM detection & pyproj coordinate transformer
│   │   ├── measurement_service.py  # Area/length calculations & graceful fallbacks
│   │   ├── storage_service.py      # Thread-safe JSON persistence & in-memory cache
│   │   └── parsers/
│   │       ├── base.py             # Abstract base dataclasses
│   │       ├── shapefile_parser.py # Secure zip extractor & pyshp reader
│   │       └── kml_parser.py       # DefusedXML parser for KML Placemarks & geometries
│   ├── static/
│   │   └── index.html              # Dark-mode interactive Leaflet map dashboard
│   └── api/
│       └── v1/
│           └── endpoints.py        # REST API route handlers
│
├── sample_data/                    # Ready-to-test sample datasets
│   ├── survey_sample.kml           # Valid KML sample (Polygons with holes, Lines, Points)
│   ├── sample_shapefile.zip        # Valid ESRI Shapefile archive with .prj WGS84
│   └── generate_samples.py         # Reproducible sample generator script
│
├── tests/                          # 100% passing Pytest test suite
│   ├── test_api.py                 # Integration tests for all REST endpoints
│   ├── test_crs_service.py         # Unit tests for UTM math & projection
│   ├── test_parsers.py             # Unit tests for KML & Shapefile parsing
│   └── test_measurements.py        # Unit tests for geometric calculations
│
├── Dockerfile                      # Production slim Docker image
├── docker-compose.yml              # Single-command container deployment
├── requirements.txt                # Pinned dependencies
├── pytest.ini                      # Pytest configuration
└── README.md                       # Comprehensive documentation
```

---

## 🚀 Quickstart & Setup

### Prerequisites
- Python 3.11 or 3.12
- Git
- *(Optional)* Docker & Docker Compose

### Option A: Local Virtual Environment Setup

1. **Clone the repository**:
   ```bash
   git clone <your-repo-link>
   cd geospatial-measurement-api
   ```

2. **Create and activate a virtual environment**:
   ```bash
   # Windows
   py -3.12 -m venv .venv
   .\.venv\Scripts\activate

   # Linux / macOS
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

4. **Run the Automated Test Suite**:
   ```bash
   pytest -v
   ```
   *(Expected output: 18 passed in ~1.2s)*

5. **Start the API Server**:
   ```bash
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```

6. **Access Interfaces**:
   - 🗺️ **Interactive Visual Web UI**: [http://localhost:8000/](http://localhost:8000/)
   - 📖 **Swagger Interactive API Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)
   - 📄 **ReDoc Documentation**: [http://localhost:8000/redoc](http://localhost:8000/redoc)

---

### Option B: Docker Setup

Run the entire application in an isolated container with one command:
```bash
docker compose up --build
```
The API and Dashboard will be live at `http://localhost:8000`.

---

## 📡 API Reference & Examples

### 1. Upload Geospatial File
`POST /api/files/`

Accepts a multipart file upload (`.zip` containing a Shapefile, or `.kml`).

```bash
curl -X POST "http://localhost:8000/api/files/" \
  -H "accept: application/json" \
  -H "Content-Type: multipart/form-data" \
  -F "file=@sample_data/survey_sample.kml"
```

**Response (`201 Created`):**
```json
{
  "id": "e4f8b91a",
  "filename": "survey_sample.kml",
  "feature_count": 4,
  "crs": "EPSG:4326",
  "status": "COMPLETED",
  "file_type": "KML",
  "uploaded_at": "2026-10-08T10:45:10.123456+00:00"
}
```

---

### 2. Get File Information
`GET /api/files/{id}/`

Returns file metadata, feature count, detected CRS, and processing status.

```bash
curl -X GET "http://localhost:8000/api/files/e4f8b91a/"
```

**Response (`200 OK`):**
```json
{
  "id": "e4f8b91a",
  "filename": "survey_sample.kml",
  "feature_count": 4,
  "crs": "EPSG:4326",
  "status": "COMPLETED"
}
```

---

### 3. Get Feature Measurements
`GET /api/files/{id}/measurements/`

Returns computed metric measurements, projected CRS details, and feature attributes.

```bash
curl -X GET "http://localhost:8000/api/files/e4f8b91a/measurements/"
```

**Sample Response (`200 OK`):**
```json
{
  "id": "e4f8b91a",
  "filename": "survey_sample.kml",
  "crs": "EPSG:4326",
  "summary": {
    "total_features": 4,
    "polygon_count": 2,
    "linestring_count": 1,
    "point_count": 1,
    "unsupported_count": 0,
    "total_area_sq_meters": 1354820.50,
    "total_area_hectares": 135.4820,
    "total_length_meters": 3120.45,
    "total_length_kilometers": 3.1205
  },
  "features": [
    {
      "feature_id": "Solar Plant Sector A",
      "geometry_type": "Polygon",
      "geometry": {
        "type": "Polygon",
        "coordinates": [[[77.585, 12.97], [77.595, 12.97], [77.595, 12.98], [77.585, 12.98], [77.585, 12.97]]]
      },
      "crs": "EPSG:4326",
      "projected_crs": "EPSG:32643 (UTM Zone 43N)",
      "properties": {
        "Project": "Aereo CleanEnergy Phase 1",
        "SurveyDate": "2026-10-08"
      },
      "measurement": {
        "measurement_type": "area",
        "value": 1184200.25,
        "unit": "square_meters",
        "formatted": "1,184,200.25 m² (118.4200 ha | 1.1842 km²)",
        "alternative_units": {
          "hectares": 118.42,
          "square_kilometers": 1.1842,
          "acres": 292.62
        }
      },
      "status": "SUCCESS",
      "notes": "Area calculated via metric projected CRS transformation."
    },
    {
      "feature_id": "Transmission Line Corridor",
      "geometry_type": "LineString",
      "geometry": {
        "type": "LineString",
        "coordinates": [[[77.58, 12.965], [77.586, 12.972], [77.592, 12.978], [77.6, 12.985]]]
      },
      "crs": "EPSG:4326",
      "projected_crs": "EPSG:32643 (UTM Zone 43N)",
      "properties": {
        "Voltage": "66kV"
      },
      "measurement": {
        "measurement_type": "length",
        "value": 3120.45,
        "unit": "meters",
        "formatted": "3,120.45 m (3.1205 km | 10,237.70 ft)",
        "alternative_units": {
          "kilometers": 3.1205,
          "feet": 10237.7
        }
      },
      "status": "SUCCESS",
      "notes": "Length calculated via metric projected CRS transformation."
    },
    {
      "feature_id": "GCP Base Station #1",
      "geometry_type": "Point",
      "geometry": {
        "type": "Point",
        "coordinates": [77.59, 12.975, 920.5]
      },
      "crs": "EPSG:4326",
      "projected_crs": null,
      "properties": {},
      "measurement": {
        "measurement_type": "none",
        "value": null,
        "unit": "none",
        "formatted": "N/A (Points have zero dimension)",
        "alternative_units": {}
      },
      "status": "SUCCESS",
      "notes": "Point geometry does not possess area or length. Handled gracefully."
    }
  ]
}
```

---

## 💡 Design Decisions & Alternatives

| Decision | Chosen Approach | Alternative Considered | Rationale |
| :--- | :--- | :--- | :--- |
| **Framework** | **FastAPI** | Django + DRF | FastAPI provides native async concurrency, automatic OpenAPI schema generation, strict Pydantic v2 validation, and significantly lower memory overhead for high-throughput microservices. |
| **CRS Metric Projection** | **Dynamic UTM Zone Selection** | Web Mercator (EPSG:3857) or Equal Earth | Web Mercator severely distorts distances and areas away from the equator (up to 400%+ near poles). UTM zones provide minimal scale distortion ($< 0.1\%$) within each 6-degree zone, matching aerial drone survey industry standards. |
| **Shapefile Parser** | **`pyshp` (`shapefile`)** | `GDAL` / `ogr2ogr` bindings | GDAL native C++ dependencies often suffer from distribution and cross-platform installation issues. `pyshp` is pure Python, fast, and eliminates heavy compilation overhead while reading `.shp`, `.shx`, `.dbf`, and `.prj` reliably. |
| **KML Parser** | **`defusedxml` + Shapely** | `fastkml` / `pykml` | Standard KML libraries frequently crash when encountering non-standard XML namespaces or missing tags. Our custom defensive parser strips arbitrary namespaces, handles 3D altitudes, interior polygon rings (holes), and protects against XXE exploits. |
| **Persistence** | **Hybrid In-Memory + File JSON Store** | PostgreSQL / SQLite | For a lightweight measurement service, this avoids external database setup friction while maintaining thread-safe $O(1)$ memory retrieval and surviving server restarts. |

---

## 📈 Learnings & Future Scope

### Learnings
1. **Ellipsoidal vs Planar Distortions**: Exploring why simple Euclidean planar calculations on geographical coordinates produce astronomical errors, and how conformal transverse Mercator cylinders minimize local projective deformation.
2. **KML Namespace Variations**: Handling inconsistent XML schemas exported by different surveying equipment (Google Earth vs DJI Terra vs Pix4D).
3. **Defensive Geospatial Parsing**: Understanding geometry self-intersections (bow-tie polygons) and how automated geometric sanitization (`shapely.validation.make_valid`) ensures zero crashes.

### Future Scope (Enterprise Aereo Cloud Scale)
- **Asynchronous Task Queue (Celery + Redis)**: Offload files with $> 100,000$ vector vertices to distributed background workers, providing WebSocket progress notifications.
- **Cloud-Optimized Formats**: Support direct streaming of **FlatGeobuf**, **GeoParquet**, and **Cloud-Optimized GeoTIFF (COG)** without requiring full file decompression.
- **S3 Presigned Direct Upload**: Client uploads large multi-gigabyte drone surveys directly to AWS S3, triggering event-driven Lambda/ECS processing workers.
- **PostGIS / SpatiaLite Integration**: Store features in spatial tables with spatial R-tree indexing (`GIST`) for fast spatial intersection queries (`ST_Intersects`, `ST_Contains`).

---

## 👨‍💻 Author
**Sanskar Sinha**  
Full Stack & Scalable Systems Engineer  
- **Email**: sanskarsinha225@gmail.com
- **LinkedIn**: [Sanskar Sinha](https://www.linkedin.com/)
- **GitHub**: [github.com/sanskar225](https://github.com/)
