import pytest
from pathlib import Path
from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)
SAMPLE_DIR = Path(__file__).parent.parent / "sample_data"

def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "HEALTHY"

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

    file_id = data["id"]

    # Test GET /api/files/{id}/
    info_res = client.get(f"/api/files/{file_id}/")
    assert info_res.status_code == 200
    info_data = info_res.json()
    assert info_data["id"] == file_id
    assert info_data["status"] == "COMPLETED"

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

    # Verify features structure
    features = meas_data["features"]
    assert len(features) >= 4
    for feat in features:
        assert "feature_id" in feat
        assert "geometry_type" in feat
        assert "geometry" in feat
        assert "crs" in feat
        assert "properties" in feat
        assert "measurement" in feat

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
