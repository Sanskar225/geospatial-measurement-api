import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
SAMPLE_DIR = Path(__file__).parent.parent / "sample_data"

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "HEALTHY"
    assert data["database"] == "CONNECTED"
    assert "X-Process-Time" in response.headers

def test_upload_kml_flow():
    kml_path = SAMPLE_DIR / "survey_sample.kml"
    with open(kml_path, "rb") as f:
        response = client.post(
            "/api/files/",
            files={"file": ("survey_sample.kml", f, "application/vnd.google-earth.kml+xml")}
        )
    
    assert response.status_code == 201
    data = response.json()
    assert "id" in data
    assert data["filename"] == "survey_sample.kml"
    assert data["feature_count"] >= 4
    assert data["status"] == "COMPLETED"
    assert data["crs"] == "EPSG:4326"
    assert "file_hash_sha256" in data

    file_id = data["id"]

    # Test GET /api/files/{id}/
    info_res = client.get(f"/api/files/{file_id}/")
    assert info_res.status_code == 200
    info_data = info_res.json()
    assert info_data["id"] == file_id
    assert info_data["status"] == "COMPLETED"
    assert info_data["progress_pct"] == 100

    # Test GET /api/files/{id}/measurements/
    meas_res = client.get(f"/api/files/{file_id}/measurements/")
    assert meas_res.status_code == 200
    meas_data = meas_res.json()
    assert meas_data["id"] == file_id
    assert "summary" in meas_data
    assert meas_data["summary"]["polygon_count"] >= 2
    assert meas_data["summary"]["linestring_count"] >= 1
    assert meas_data["summary"]["point_count"] >= 1
    assert meas_data["summary"]["total_area_sq_meters"] > 0
    assert meas_data["summary"]["total_length_meters"] > 0

    # Verify advanced GIS metrics in features
    features = meas_data["features"]
    assert len(features) >= 4
    poly_feat = next(f for f in features if f["geometry_type"] == "Polygon")
    assert poly_feat["measurement"]["advanced_metrics"] is not None
    assert poly_feat["measurement"]["advanced_metrics"]["geodesic_value"] is not None
    assert poly_feat["measurement"]["advanced_metrics"]["distortion_percentage"] is not None

def test_sha256_deduplication():
    # Uploading the exact same file should return existing record (idempotency)
    kml_path = SAMPLE_DIR / "survey_sample.kml"
    with open(kml_path, "rb") as f:
        res1 = client.post("/api/files/", files={"file": ("survey_sample.kml", f, "text/xml")})
    with open(kml_path, "rb") as f:
        res2 = client.post("/api/files/", files={"file": ("survey_sample.kml", f, "text/xml")})

    assert res1.status_code == 201
    assert res2.status_code == 201
    assert res1.json()["id"] == res2.json()["id"]
    assert res1.json()["file_hash_sha256"] == res2.json()["file_hash_sha256"]

def test_export_endpoints():
    kml_path = SAMPLE_DIR / "survey_sample.kml"
    with open(kml_path, "rb") as f:
        upload_res = client.post("/api/files/", files={"file": ("survey_sample.kml", f, "text/xml")})
    file_id = upload_res.json()["id"]

    # Test GeoJSON export
    geo_res = client.get(f"/api/files/{file_id}/export?format=geojson")
    assert geo_res.status_code == 200
    geo_data = geo_res.json()
    assert geo_data["type"] == "FeatureCollection"
    assert len(geo_data["features"]) >= 4

    # Test CSV export
    csv_res = client.get(f"/api/files/{file_id}/export?format=csv")
    assert csv_res.status_code == 200
    assert "text/csv" in csv_res.headers["content-type"]
    assert "Feature_ID,Geometry_Type" in csv_res.text

def test_pagination_and_filtering():
    kml_path = SAMPLE_DIR / "survey_sample.kml"
    with open(kml_path, "rb") as f:
        upload_res = client.post("/api/files/", files={"file": ("survey_sample.kml", f, "text/xml")})
    file_id = upload_res.json()["id"]

    # Filter only Polygons with limit=1
    res = client.get(f"/api/files/{file_id}/measurements/?geometry_type=Polygon&limit=1&offset=0")
    assert res.status_code == 200
    data = res.json()
    assert len(data["features"]) == 1
    assert data["features"][0]["geometry_type"] == "Polygon"
    assert data["total_count"] >= 2

def test_upload_shapefile_zip_flow():
    shp_path = SAMPLE_DIR / "sample_shapefile.zip"
    with open(shp_path, "rb") as f:
        response = client.post(
            "/api/files/",
            files={"file": ("sample_shapefile.zip", f, "application/zip")}
        )
    
    assert response.status_code == 201
    data = response.json()
    assert data["filename"] == "sample_shapefile.zip"
    assert data["feature_count"] == 2
    assert data["status"] == "COMPLETED"

    file_id = data["id"]
    meas_res = client.get(f"/api/files/{file_id}/measurements/")
    assert meas_res.status_code == 200
    meas_data = meas_res.json()
    assert meas_data["summary"]["polygon_count"] == 2
    assert meas_data["summary"]["total_area_sq_meters"] > 0

def test_upload_invalid_extension():
    response = client.post(
        "/api/files/",
        files={"file": ("test.txt", b"invalid contents", "text/plain")}
    )
    assert response.status_code == 400
    assert "Unsupported file extension" in response.json()["detail"]

def test_get_nonexistent_file():
    response = client.get("/api/files/nonexistent_id/")
    assert response.status_code == 404

def test_list_files():
    response = client.get("/api/files/")
    assert response.status_code == 200
    files_list = response.json()
    assert isinstance(files_list, list)
    assert len(files_list) >= 1
