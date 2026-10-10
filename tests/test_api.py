"""End-to-end tests: every request goes through the real ASGI app, service layer and SQLite database."""

import asyncio
import csv
import datetime
import io
import json
import re
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pyproj import Geod

from app.core.config import settings
from app.services.file_service import file_service
from app.services.parsers import ParsedDataset, ParserRegistry
from tests.helpers import (
    WGS84_ESRI_WKT,
    kml,
    kml_line,
    kml_point,
    kml_polygon,
    make_shapefile_zip,
    square,
    zip_of,
)

SAMPLE_DIR = Path(__file__).resolve().parent.parent / "sample_data"
GEOD = Geod(ellps="WGS84")
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
CORRUPT_ZIP = b"PK\x03\x04" + b"this is not really a zip archive" * 4


def unique(content: bytes) -> bytes:
    """Append a unique XML comment so the SHA-256 differs and the upload is not de-duplicated."""
    return content.replace(b"</Document>", f"<!-- {uuid.uuid4().hex} --></Document>".encode())


def geodesic_polygon_area(shell, holes=()) -> float:
    """Independent reference value: area on the WGS-84 ellipsoid (holes subtracted)."""

    def ring_area(ring):
        lons, lats = zip(*ring, strict=True)
        return abs(GEOD.polygon_area_perimeter(lons, lats)[0])

    return ring_area(shell) - sum(ring_area(h) for h in holes)


def geodesic_line_length(coords) -> float:
    lons, lats = zip(*coords, strict=True)
    return GEOD.line_length(lons, lats)


def upload_dir_files() -> set[str]:
    return {p.name for p in settings.UPLOAD_DIR.iterdir()}


def measurements(client, file_id: str, **params) -> dict:
    response = client.get(f"/api/files/{file_id}/measurements/", params=params)
    assert response.status_code == 200, response.text
    return response.json()


# ====================================================================== the assignment's flow
class TestSpecFlow:
    def test_kml_upload_info_and_measurements(self, client, upload):
        created = upload("survey_sample.kml", unique((SAMPLE_DIR / "survey_sample.kml").read_bytes()))
        assert created.status_code == 201
        body = created.json()
        file_id = body["id"]
        assert re.fullmatch(r"[0-9a-f]{32}", file_id)
        assert body["filename"] == "survey_sample.kml"
        assert body["status"] == "COMPLETED" and body["progress_pct"] == 100
        assert body["feature_count"] == 4 and body["crs"] == "EPSG:4326" and body["file_type"] == "KML"
        assert created.headers["Location"] == f"/api/files/{file_id}/"

        info = client.get(f"/api/files/{file_id}/")
        assert info.status_code == 200
        assert info.json() == body

        data = measurements(client, file_id)
        assert [f["geometry_type"] for f in data["features"]] == ["Polygon", "LineString", "Point", "Polygon"]
        assert [f["index"] for f in data["features"]] == [0, 1, 2, 3]
        for feature in data["features"]:  # the per-feature fields the assignment asks for
            assert {"feature_id", "index", "geometry_type", "geometry", "crs", "properties"} <= feature.keys()
            assert feature["crs"] == "EPSG:4326"

        site, line, point, pit = data["features"]
        assert site["properties"]["name"] == "Solar Plant Sector A"
        assert site["properties"]["Project"] == "Aereo CleanEnergy Phase 1"

        # Polygon -> area in square metres, cross-checked against an independent geodesic computation
        ring = [(77.585, 12.97), (77.595, 12.97), (77.595, 12.98), (77.585, 12.98), (77.585, 12.97)]
        assert site["measurement"]["measurement_type"] == "area"
        assert site["measurement"]["unit"] == "square_meters"
        assert site["measurement"]["value"] == pytest.approx(geodesic_polygon_area(ring), rel=0.003)
        assert site["projected_crs"] == "EPSG:32643"  # UTM 43N, a metric CRS - never degrees
        assert site["measurement"]["advanced_metrics"]["distortion_percentage"] < 0.3

        # LineString -> length in metres
        line_coords = [(77.58, 12.965), (77.586, 12.972), (77.592, 12.978), (77.6, 12.985)]
        assert line["measurement"]["measurement_type"] == "length"
        assert line["measurement"]["unit"] == "meters"
        assert line["measurement"]["value"] == pytest.approx(geodesic_line_length(line_coords), rel=0.003)

        # Point -> no measurement, and nothing to warn about
        assert point["measurement"]["measurement_type"] == "none"
        assert point["measurement"]["value"] is None
        assert point["status"] == "SUCCESS" and point["notes"] == []

        # Polygon with a hole: the hole must be subtracted (this is the orientation bug a naive
        # geodesic computation falls into)
        shell = [(77.6, 12.96), (77.615, 12.96), (77.615, 12.975), (77.6, 12.975), (77.6, 12.96)]
        hole = [(77.604, 12.964), (77.61, 12.964), (77.61, 12.97), (77.604, 12.97), (77.604, 12.964)]
        expected = geodesic_polygon_area(shell, [hole])
        assert 2.2e6 < pit["measurement"]["value"] < 2.3e6
        assert pit["measurement"]["value"] == pytest.approx(expected, rel=0.003)
        assert pit["measurement"]["advanced_metrics"]["distortion_percentage"] < 0.3

        summary = data["summary"]
        assert summary["total_features"] == 4
        assert (summary["polygon_count"], summary["linestring_count"], summary["point_count"]) == (2, 1, 1)
        assert summary["total_area_sq_meters"] == pytest.approx(
            site["measurement"]["value"] + pit["measurement"]["value"], abs=0.02
        )
        assert summary["total_length_meters"] == pytest.approx(line["measurement"]["value"], abs=0.02)

    def test_shapefile_zip(self, client, upload):
        data = make_shapefile_zip(
            [square(77.58, 12.97, 0.01), square(77.60, 12.98, 0.02)],
            [("Plot A",), ("Plot B",)],
            crs=WGS84_ESRI_WKT,
        )
        created = upload("plots.zip", data)
        assert created.status_code == 201
        body = created.json()
        assert body["file_type"] == "Shapefile (.zip)" and body["feature_count"] == 2 and body["crs"] == "EPSG:4326"
        assert body["warnings"] == []

        features = measurements(client, body["id"])["features"]
        assert [f["properties"]["NAME"] for f in features] == ["Plot A", "Plot B"]
        assert features[0]["measurement"]["value"] == pytest.approx(
            geodesic_polygon_area(square(77.58, 12.97, 0.01)), rel=0.003
        )
        # a 4x larger square (twice the side) has ~4x the area
        assert features[1]["measurement"]["value"] / features[0]["measurement"]["value"] == pytest.approx(4, rel=0.01)

    def test_projected_shapefile_is_remeasured_and_reprojected_for_maps(self, client, upload):
        """A UTM shapefile (units: metres): coordinates are not lon/lat, so the map needs geometry_wgs84."""
        data = make_shapefile_zip([square(500_000, 1_435_000, 100)], crs="EPSG:32643")
        created = upload("utm.zip", data)
        assert created.status_code == 201 and created.json()["crs"] == "EPSG:32643"
        feature = measurements(client, created.json()["id"])["features"][0]

        assert feature["crs"] == "EPSG:32643"
        assert feature["measurement"]["value"] == pytest.approx(10_000, rel=0.002)
        lon, lat = feature["geometry_wgs84"]["coordinates"][0][0]
        assert lon == pytest.approx(75.0, abs=0.01) and lat == pytest.approx(12.98, abs=0.05)
        assert feature["geometry"]["coordinates"][0][0][0] in (500_000, 500_100)  # original coordinates kept

    def test_packaged_sample_files_work(self, client, upload):
        for name in ("sample_shapefile.zip", "sample_utm_shapefile.zip"):
            response = upload(name, (SAMPLE_DIR / name).read_bytes())
            assert response.status_code in (200, 201), response.text
            data = measurements(client, response.json()["id"])
            assert data["summary"]["polygon_count"] == 2
            assert all(f["status"] == "SUCCESS" for f in data["features"])

    def test_kmz(self, client, upload):
        created = upload("tour.kmz", zip_of({"doc.kml": kml(kml_polygon(), kml_line(), kml_point())}))
        assert created.status_code == 201
        assert created.json()["file_type"] == "KMZ" and created.json()["feature_count"] == 3

    def test_dbf_date_columns_no_longer_crash(self, client, upload):
        data = make_shapefile_zip(
            [square(77.58, 12.97, 0.01)],
            [("A", datetime.date(2026, 10, 8))],
            fields=(("NAME", "C", 20), ("SURVEYED", "D")),
            crs=WGS84_ESRI_WKT,
        )
        created = upload("dated.zip", data)
        assert created.status_code == 201
        feature = measurements(client, created.json()["id"])["features"][0]
        assert feature["properties"]["SURVEYED"] == "2026-10-08"

    def test_bad_features_are_reported_individually_never_fail_the_file(self, client, upload):
        bad_coords = kml_polygon("outside-range", x=200, y=100)
        malformed = (
            "<Placemark><name>broken</name><LineString><coordinates>abc,def</coordinates></LineString></Placemark>"
        )
        collection = (
            "<Placemark><name>mixed</name><MultiGeometry>"
            "<Point><coordinates>77.58,12.97,0</coordinates></Point>"
            "<LineString><coordinates>77.58,12.97,0 77.59,12.98,0</coordinates></LineString>"
            "</MultiGeometry></Placemark>"
        )
        created = upload("mixed.kml", kml(kml_polygon("good"), bad_coords, malformed, collection, kml_point()))
        assert created.status_code == 201
        body = created.json()
        assert body["status"] == "COMPLETED" and body["feature_count"] == 5
        assert any("could not be measured" in w for w in body["warnings"])

        data = measurements(client, body["id"])
        assert [f["status"] for f in data["features"]] == [
            "SUCCESS",
            "INVALID_COORDINATES",
            "INVALID_GEOMETRY",
            "UNSUPPORTED_GEOMETRY",
            "SUCCESS",
        ]
        by_name = {f["properties"]["name"]: f for f in data["features"]}
        assert "Malformed coordinate" in by_name["broken"]["notes"][0]
        assert "GeometryCollection" in by_name["mixed"]["notes"][0]
        assert by_name["outside-range"]["measurement"] is None
        assert by_name["outside-range"]["geometry"] is not None  # original data still returned

        summary = data["summary"]
        assert summary["total_features"] == 5
        assert (summary["polygon_count"], summary["point_count"], summary["linestring_count"]) == (2, 1, 0)
        assert (summary["unsupported_count"], summary["failed_count"]) == (1, 2)
        assert summary["total_area_sq_meters"] == pytest.approx(by_name["good"]["measurement"]["value"], abs=0.02)

    def test_invalid_polygon_is_repaired_and_flagged(self, client, upload):
        bowtie = "77.58,12.97,0 77.59,12.98,0 77.59,12.97,0 77.58,12.98,0 77.58,12.97,0"
        placemark = (
            "<Placemark><name>bowtie</name><Polygon><outerBoundaryIs><LinearRing>"
            f"<coordinates>{bowtie}</coordinates></LinearRing></outerBoundaryIs></Polygon></Placemark>"
        )
        created = upload("bowtie.kml", kml(placemark))
        feature = measurements(client, created.json()["id"])["features"][0]
        assert feature["status"] == "SUCCESS"
        assert feature["measurement"]["value"] > 0
        assert any("make_valid" in note for note in feature["notes"])


# ====================================================================== querying
class TestQuerying:
    @pytest.fixture(scope="class")
    def file_id(self, client):
        multi = (
            "<Placemark><name>multi</name><MultiGeometry>"
            + "".join(
                f"<Polygon><outerBoundaryIs><LinearRing><coordinates>"
                f"{' '.join(f'{x},{y},0' for x, y in square(77.7 + i * 0.01, 12.9, 0.005))}"
                "</coordinates></LinearRing></outerBoundaryIs></Polygon>"
                for i in range(2)
            )
            + "</MultiGeometry></Placemark>"
        )
        broken = "<Placemark><name>broken</name><Point><coordinates>oops</coordinates></Point></Placemark>"
        placemarks = (
            [kml_polygon(f"poly-{i}", x=77.5 + i * 0.02) for i in range(5)]
            + [kml_line(f"line-{i}", f"77.5,12.9,0 77.5{i + 1},12.95,0") for i in range(3)]
            + [kml_point(f"pt-{i}", f"77.5{i},12.9,0") for i in range(2)]
            + [multi, broken]
        )
        response = client.post("/api/files/", files={"file": ("query.kml", kml(*placemarks), "text/xml")})
        assert response.status_code == 201
        return response.json()["id"]

    def test_pagination(self, client, file_id):
        first = measurements(client, file_id, limit=5)
        assert (first["total_count"], first["limit"], first["offset"], first["has_more"]) == (12, 5, 0, True)
        assert [f["index"] for f in first["features"]] == [0, 1, 2, 3, 4]

        last = measurements(client, file_id, limit=5, offset=10)
        assert [f["index"] for f in last["features"]] == [10, 11]
        assert last["has_more"] is False

        assert measurements(client, file_id, limit=5, offset=100)["features"] == []

    def test_summary_ignores_filters_and_pagination(self, client, file_id):
        data = measurements(client, file_id, geometry_type="Point", limit=1)
        assert len(data["features"]) == 1
        assert data["summary"]["total_features"] == 12
        assert data["summary"]["polygon_count"] == 6  # 5 Polygon + 1 MultiPolygon
        assert data["summary"]["point_count"] == 2  # the unparseable point has no geometry, so it is not counted

    def test_geometry_type_filter_is_exact(self, client, file_id):
        polygons = measurements(client, file_id, geometry_type="Polygon")
        assert polygons["total_count"] == 5
        assert {f["geometry_type"] for f in polygons["features"]} == {"Polygon"}
        multi = measurements(client, file_id, geometry_type="MultiPolygon")
        assert multi["total_count"] == 1 and multi["features"][0]["properties"]["name"] == "multi"
        assert measurements(client, file_id, geometry_type="LineString")["total_count"] == 3

    def test_status_filter_and_combination(self, client, file_id):
        bad = measurements(client, file_id, status="INVALID_GEOMETRY")
        assert bad["total_count"] == 1 and bad["features"][0]["properties"]["name"] == "broken"
        both = measurements(client, file_id, geometry_type="Polygon", status="SUCCESS")
        assert both["total_count"] == 5
        assert measurements(client, file_id, geometry_type="Point", status="INVALID_GEOMETRY")["total_count"] == 0

    @pytest.mark.parametrize(
        "params",
        [
            {"geometry_type": "Banana"},
            {"status": "MAYBE"},
            {"limit": 0},
            {"limit": 1001},
            {"offset": -1},
            {"limit": "many"},
        ],
    )
    def test_invalid_query_parameters_are_422(self, client, file_id, params):
        response = client.get(f"/api/files/{file_id}/measurements/", params=params)
        assert response.status_code == 422

    def test_list_files(self, client, upload):
        created = upload("listed.kml", kml(kml_polygon()))
        newest = created.json()["id"]
        response = client.get("/api/files/", params={"limit": 2})
        assert response.status_code == 200
        assert response.json()[0]["id"] == newest  # newest first
        assert len(response.json()) <= 2
        assert int(response.headers["X-Total-Count"]) >= 1
        assert client.get("/api/files/", params={"limit": 0}).status_code == 422
        assert client.get("/api/files/", params={"offset": -5}).status_code == 422


# ====================================================================== de-duplication
class TestDeduplication:
    def test_identical_bytes_return_the_existing_record(self, client, upload):
        content = kml(kml_polygon())
        first = upload("a.kml", content)
        files_after_first = upload_dir_files()
        second = upload("renamed.kml", content)
        assert first.status_code == 201 and second.status_code == 200
        assert second.json()["id"] == first.json()["id"]
        assert second.json()["filename"] == "a.kml"
        assert upload_dir_files() == files_after_first  # the duplicate was not kept on disk

    def test_different_bytes_are_new_records(self, upload):
        first, second = upload("a.kml", kml(kml_polygon())), upload("b.kml", kml(kml_polygon()))
        assert first.status_code == second.status_code == 201
        assert first.json()["id"] != second.json()["id"]
        assert first.json()["file_hash_sha256"] != second.json()["file_hash_sha256"]

    def test_failed_uploads_are_not_deduplicated(self, upload):
        first, second = upload("bad.zip", CORRUPT_ZIP), upload("bad.zip", CORRUPT_ZIP)
        assert first.status_code == second.status_code == 400
        assert first.json()["file_id"] != second.json()["file_id"]

    def test_disabled_by_setting(self, upload, monkeypatch):
        monkeypatch.setattr(file_service._config, "DEDUPLICATE_UPLOADS", False)
        content = kml(kml_polygon())
        assert upload("a.kml", content).status_code == 201
        assert upload("a.kml", content).status_code == 201


# ====================================================================== errors
class TestErrors:
    def test_unsupported_extension_is_415_and_nothing_is_stored(self, upload):
        before = upload_dir_files()
        response = upload("notes.txt", b"hello")
        assert response.status_code == 415
        assert response.json()["error_code"] == "UNSUPPORTED_FILE_TYPE"
        assert ".kml" in response.json()["detail"]
        assert upload_dir_files() == before

    def test_no_extension(self, upload):
        assert upload("README", b"hello").status_code == 415

    def test_missing_file_part_is_422(self, client):
        assert client.post("/api/files/").status_code == 422

    def test_empty_file(self, upload):
        response = upload("empty.kml", b"")
        assert response.status_code == 400 and response.json()["error_code"] == "INVALID_FILE"

    @pytest.mark.parametrize(
        ("name", "content"), [("photo.kml", PNG_BYTES), ("data.zip", b"hello"), ("x.kmz", b"<kml/>")]
    )
    def test_wrong_signature_is_rejected_before_parsing(self, upload, name, content):
        before = upload_dir_files()
        response = upload(name, content)
        assert response.status_code == 400 and response.json()["error_code"] == "INVALID_FILE"
        assert upload_dir_files() == before
        assert "file_id" not in response.json()  # nothing was registered

    def test_corrupt_zip_marks_the_file_failed(self, client, upload):
        before = upload_dir_files()
        response = upload("broken.zip", CORRUPT_ZIP)
        assert response.status_code == 400
        body = response.json()
        assert body["error_code"] == "INVALID_FILE" and body["file_id"]
        assert upload_dir_files() == before  # failed uploads are cleaned from disk

        info = client.get(f"/api/files/{body['file_id']}/").json()
        assert info["status"] == "FAILED" and info["error_message"]

        for path in ("measurements/", "export"):
            result = client.get(f"/api/files/{body['file_id']}/{path}")
            assert result.status_code == 422
            assert result.json()["error_code"] == "FILE_PROCESSING_FAILED"
            assert result.json()["file_id"] == body["file_id"]

    def test_zip_without_a_shapefile(self, upload):
        response = upload("docs.zip", zip_of({"readme.txt": b"nothing geospatial here"}))
        assert response.status_code == 400 and ".shp" in response.json()["detail"]

    def test_kml_without_placemarks(self, upload):
        response = upload("empty.kml", kml())
        assert response.status_code == 400 and "Placemark" in response.json()["detail"]

    def test_malformed_xml(self, upload):
        assert upload("bad.kml", b"<kml><Document><Placemark>").status_code == 400

    def test_xxe_and_entity_expansion_are_rejected(self, upload):
        xxe = (
            b'<?xml version="1.0"?><!DOCTYPE kml [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            b'<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Placemark><name>&xxe;</name>'
            b"<Point><coordinates>77,12,0</coordinates></Point></Placemark></Document></kml>"
        )
        response = upload("xxe.kml", xxe)
        assert response.status_code == 400
        assert "root:" not in response.text

        bomb = (
            b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'
            b"<kml><Document><Placemark><name>&b;</name></Placemark></Document></kml>"
        )
        assert upload("bomb.kml", bomb).status_code == 400

    def test_missing_prj_with_projected_coordinates_is_422(self, client, upload):
        data = make_shapefile_zip([square(500_000, 1_435_000, 100)])  # metres, but no .prj
        response = upload("noprj.zip", data)
        assert response.status_code == 422
        body = response.json()
        assert body["error_code"] == "CRS_MISSING" and ".prj" in body["detail"]
        assert client.get(f"/api/files/{body['file_id']}/").json()["status"] == "FAILED"

    def test_missing_prj_with_lonlat_coordinates_assumes_wgs84_and_says_so(self, client, upload):
        data = make_shapefile_zip([square(77.58, 12.97, 0.01)])  # no .prj, but clearly lon/lat
        response = upload("noprj-lonlat.zip", data)
        assert response.status_code == 201
        body = response.json()
        assert body["crs"] == "EPSG:4326"
        assert any("assumed" in w for w in body["warnings"])
        assert client.get(f"/api/files/{body['id']}/").json()["warnings"] == body["warnings"]

    def test_unknown_ids_are_404(self, client):
        missing = "0" * 32
        for method, path in [
            ("get", f"/api/files/{missing}/"),
            ("get", f"/api/files/{missing}/measurements/"),
            ("get", f"/api/files/{missing}/export"),
            ("delete", f"/api/files/{missing}/"),
        ]:
            response = getattr(client, method)(path)
            assert response.status_code == 404, path
            assert response.json()["error_code"] == "FILE_NOT_FOUND"
            assert response.json()["file_id"] == missing

    def test_overlong_id_is_422(self, client):
        assert client.get(f"/api/files/{'x' * 65}/").status_code == 422

    def test_hostile_filename_cannot_escape_the_upload_folder(self, client, upload):
        content = kml(kml_polygon())
        before = upload_dir_files()
        response = upload("../../../tmp/evil.kml", content)
        assert response.status_code == 201
        assert response.json()["filename"] == "evil.kml"
        new_files = upload_dir_files() - before
        assert len(new_files) == 1 and new_files.pop().startswith(response.json()["id"])
        assert not Path("/tmp/evil.kml").exists()

    def test_no_internal_paths_in_error_bodies(self, upload):
        response = upload("broken.zip", CORRUPT_ZIP)
        assert str(settings.UPLOAD_DIR) not in response.text and "Traceback" not in response.text

    def test_413_when_streamed_size_exceeds_the_limit(self, upload, monkeypatch):
        monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE_MB", 0.001)  # ~1 KB
        before = upload_dir_files()
        response = upload("big.kml", b"<kml>" + b"x" * 5000 + b"</kml>")
        assert response.status_code == 413
        assert response.json()["error_code"] == "FILE_TOO_LARGE" and "File exceeds" in response.json()["detail"]
        assert upload_dir_files() == before  # partial file removed

    def test_413_from_content_length_guard_before_the_body_is_read(self, upload, monkeypatch):
        monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE_MB", 0.001)
        response = upload("huge.zip", b"PK\x03\x04" + b"\x00" * 200_000)
        assert response.status_code == 413
        assert response.json()["error_code"] == "FILE_TOO_LARGE" and "Request body" in response.json()["detail"]

    def test_unhandled_exception_returns_a_generic_500(self, client, monkeypatch):
        monkeypatch.setattr(
            file_service, "get_info", AsyncMock(side_effect=RuntimeError("secret internals at /srv/app/db.sqlite"))
        )
        lenient = TestClient(client.app, raise_server_exceptions=False)  # we want the response, not the re-raise
        response = lenient.get("/api/files/abc/")
        assert response.status_code == 500
        assert response.json() == {"detail": "Internal server error.", "error_code": "INTERNAL_ERROR"}
        assert "secret" not in response.text

    def test_parser_crash_is_a_500_that_marks_the_file_failed(self, client, upload, monkeypatch):
        class Exploding:
            file_types = {".kml": "KML"}

            def parse(self, path):
                raise RuntimeError("boom at /secret/place")

        monkeypatch.setattr(file_service, "_registry", ParserRegistry([Exploding()]))
        before = upload_dir_files()
        response = upload("crash.kml", kml(kml_polygon()))
        assert response.status_code == 500
        body = response.json()
        assert body["error_code"] == "PROCESSING_ERROR" and body["file_id"]
        assert "boom" not in response.text and "secret" not in response.text
        assert upload_dir_files() == before

        info = client.get(f"/api/files/{body['file_id']}/").json()
        assert info["status"] == "FAILED" and "boom" not in info["error_message"]


# ====================================================================== background mode
class TestBackgroundMode:
    @pytest.fixture
    def pending_file(self, upload):
        """Registers a file but never processes it (as if the worker had not got to it yet)."""

        def _make() -> str:
            with pytest.MonkeyPatch.context() as patch:
                patch.setattr(file_service, "process", AsyncMock())
                response = upload("pending.kml", kml(kml_polygon()), background="true")
            assert response.status_code == 202
            return response.json()["id"]

        return _make

    def test_accepted_then_completed(self, client, upload):
        response = upload("bg.kml", kml(kml_polygon(), kml_line()), background="true")
        assert response.status_code == 202
        body = response.json()
        assert body["status"] == "PENDING" and body["feature_count"] == 0
        assert response.headers["Location"] == f"/api/files/{body['id']}/"

        info = client.get(f"/api/files/{body['id']}/").json()  # the client polls this
        assert info["status"] == "COMPLETED" and info["feature_count"] == 2 and info["progress_pct"] == 100
        assert measurements(client, body["id"])["summary"]["total_features"] == 2

    def test_results_of_an_unfinished_file_are_409(self, client, pending_file):
        file_id = pending_file()
        assert client.get(f"/api/files/{file_id}/").json()["status"] == "PENDING"
        for path in ("measurements/", "export"):
            response = client.get(f"/api/files/{file_id}/{path}")
            assert response.status_code == 409
            assert response.json()["error_code"] == "FILE_NOT_READY"

    def test_background_failure_is_recorded_on_the_file(self, client, upload):
        response = upload("bad.zip", CORRUPT_ZIP, background="true")
        assert response.status_code == 202  # accepted: the error surfaces when polling
        info = client.get(f"/api/files/{response.json()['id']}/").json()
        assert info["status"] == "FAILED" and info["error_message"]

    def test_interrupted_files_are_failed_on_startup(self, client, pending_file):
        file_id = pending_file()
        recovered = client.portal.call(file_service.recover_interrupted)
        assert recovered >= 1
        info = client.get(f"/api/files/{file_id}/").json()
        assert info["status"] == "FAILED" and "restart" in info["error_message"]
        assert client.portal.call(file_service.recover_interrupted) == 0  # idempotent

    def test_recovery_leaves_finished_files_alone(self, client, upload):
        file_id = upload("done.kml", kml(kml_polygon())).json()["id"]
        client.portal.call(file_service.recover_interrupted)
        assert client.get(f"/api/files/{file_id}/").json()["status"] == "COMPLETED"

    def test_cancelled_processing_does_not_leave_the_file_stuck(self, client, pending_file, monkeypatch):
        class Slow:
            file_types = {".kml": "KML"}

            def parse(self, path):
                time.sleep(0.6)
                return ParsedDataset(features=[], file_type="KML")

        file_id = pending_file()
        monkeypatch.setattr(file_service, "_registry", ParserRegistry([Slow()]))

        async def run():
            with pytest.raises(TimeoutError):  # stands in for the client hanging up mid-request
                await asyncio.wait_for(file_service.process(file_id), timeout=0.2)

        client.portal.call(run)
        info = client.get(f"/api/files/{file_id}/").json()
        assert info["status"] == "FAILED" and "cancelled" in info["error_message"]


# ====================================================================== export
class TestExport:
    def test_geojson(self, client, upload):
        file_id = upload("survey.kml", unique((SAMPLE_DIR / "survey_sample.kml").read_bytes())).json()["id"]
        response = client.get(f"/api/files/{file_id}/export")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/geo+json")
        assert response.headers["content-disposition"] == 'attachment; filename="survey_measurements.geojson"'

        collection = response.json()
        assert collection["type"] == "FeatureCollection" and len(collection["features"]) == 4
        polygon, line, point, _ = collection["features"]
        assert polygon["geometry"]["type"] == "Polygon"
        assert polygon["properties"]["measure_type"] == "area"
        assert polygon["properties"]["measure_unit"] == "square_meters"
        assert polygon["properties"]["measure_value"] > 0
        assert polygon["properties"]["name"] == "Solar Plant Sector A"
        assert line["properties"]["measure_type"] == "length"
        assert point["properties"]["measure_type"] == "none" and point["properties"]["measure_value"] is None

    def test_geometry_is_always_lon_lat_even_for_projected_sources(self, client, upload):
        data = make_shapefile_zip([square(500_000, 1_435_000, 100)], crs="EPSG:32643")
        file_id = upload("utm.zip", data).json()["id"]
        feature = client.get(f"/api/files/{file_id}/export").json()["features"][0]
        lon, lat = feature["geometry"]["coordinates"][0][0]
        assert -180 <= lon <= 180 and -90 <= lat <= 90 and lon == pytest.approx(75.0, abs=0.01)
        assert feature["properties"]["source_crs"] == "EPSG:32643"
        assert feature["properties"]["projected_crs"] == "EPSG:32643"

    def test_large_export_is_streamed_across_batches(self, client, upload):
        placemarks = [
            kml_polygon(f"p{i}", x=77.0 + (i % 50) * 0.002, y=12.0 + (i // 50) * 0.002, size=0.001) for i in range(2500)
        ]
        created = upload("large.kml", kml(*placemarks))
        assert created.status_code == 201 and created.json()["feature_count"] == 2500
        file_id = created.json()["id"]

        geojson = client.get(f"/api/files/{file_id}/export", params={"format": "geojson"}).json()
        assert [f["id"] for f in geojson["features"]] == list(range(2500))  # every batch, in order

        page = measurements(client, file_id, limit=1000, offset=2000)
        assert len(page["features"]) == 500 and page["has_more"] is False
        assert measurements(client, file_id, limit=1000)["has_more"] is True

        rows = list(csv.reader(io.StringIO(client.get(f"/api/files/{file_id}/export", params={"format": "csv"}).text)))
        assert len(rows) == 2501  # header + 2500

    def test_csv_columns_and_formula_injection_safety(self, client, upload):
        names = ['=HYPERLINK("http://evil.example","click")', "+SUM(1,1)", "-2+3", "@SUM(1)", "plain"]
        file_id = upload("inject.kml", kml(*[kml_polygon(n) for n in names])).json()["id"]

        response = client.get(f"/api/files/{file_id}/export", params={"format": "csv"})
        assert response.headers["content-type"].startswith("text/csv")
        assert response.headers["content-disposition"] == 'attachment; filename="inject_measurements.csv"'
        rows = list(csv.DictReader(io.StringIO(response.text)))
        assert list(rows[0]) == [
            "index", "feature_id", "name", "geometry_type", "status", "measurement_type", "value", "unit",
            "perimeter_m", "geodesic_value", "difference_pct", "source_crs", "projected_crs", "notes",
        ]  # fmt: skip
        assert [r["name"] for r in rows] == ["'" + n for n in names[:-1]] + ["plain"]
        assert all(r["status"] == "SUCCESS" and r["measurement_type"] == "area" for r in rows)
        assert all(float(r["value"]) > 0 for r in rows)  # numbers are never prefixed

    def test_csv_leaves_blank_cells_for_unmeasured_features(self, client, upload):
        file_id = upload("pts.kml", kml(kml_point("only"))).json()["id"]
        row = next(
            csv.DictReader(io.StringIO(client.get(f"/api/files/{file_id}/export", params={"format": "csv"}).text))
        )
        assert row["measurement_type"] == "none" and row["value"] == "" and row["status"] == "SUCCESS"

    def test_unknown_format_is_422(self, client, upload):
        file_id = upload("x.kml", kml(kml_polygon())).json()["id"]
        assert client.get(f"/api/files/{file_id}/export", params={"format": "xlsx"}).status_code == 422

    def test_filename_in_header_is_sanitised(self, client, upload):
        file_id = upload('we"ird\r\nname é.kml', kml(kml_polygon())).json()["id"]
        header = client.get(f"/api/files/{file_id}/export").headers["content-disposition"]
        assert re.fullmatch(r'attachment; filename="[A-Za-z0-9._-]+_measurements\.geojson"', header)


# ====================================================================== delete
class TestDelete:
    def test_delete_removes_record_results_and_file(self, client, upload):
        content = kml(kml_polygon(), kml_line())
        created = upload("gone.kml", content)
        file_id = created.json()["id"]
        stored = settings.UPLOAD_DIR / f"{file_id}.kml"
        assert stored.exists()  # KEEP_UPLOADED_FILES defaults to true

        response = client.delete(f"/api/files/{file_id}/")
        assert response.status_code == 204 and response.content == b""
        assert not stored.exists()
        for path in ("", "measurements/", "export"):
            assert client.get(f"/api/files/{file_id}/{path}").status_code == 404
        assert client.delete(f"/api/files/{file_id}/").status_code == 404

        again = upload("gone.kml", content)  # no stale de-duplication hit after deleting
        assert again.status_code == 201 and again.json()["id"] != file_id

    def test_delete_does_not_touch_other_files(self, client, upload):
        keep, drop = upload("keep.kml", kml(kml_polygon())), upload("drop.kml", kml(kml_polygon()))
        client.delete(f"/api/files/{drop.json()['id']}/")
        assert measurements(client, keep.json()["id"])["total_count"] == 1


# ====================================================================== platform / ops
class TestPlatform:
    def test_health(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "HEALTHY" and response.json()["database"] == "CONNECTED"

    def test_health_reports_503_when_the_database_is_down(self, client, monkeypatch):
        monkeypatch.setattr("app.main.check_db", AsyncMock(return_value=False))
        response = client.get("/health")
        assert response.status_code == 503
        assert response.json()["status"] == "UNHEALTHY" and response.json()["database"] == "UNAVAILABLE"

    def test_request_id_is_generated_and_echoed(self, client):
        generated = client.get("/health").headers["X-Request-ID"]
        assert re.fullmatch(r"[0-9a-f]{32}", generated)
        assert (
            client.get("/health", headers={"X-Request-ID": "trace-123.abc"}).headers["X-Request-ID"] == "trace-123.abc"
        )
        assert re.fullmatch(r"[0-9a-f]{32}", client.get("/health").headers["X-Request-ID"])
        assert client.get("/health").headers["X-Process-Time"].endswith("ms")

    @pytest.mark.parametrize("hostile", ["has spaces", "x" * 65, "new\tline", "a/b"])
    def test_hostile_request_ids_are_replaced(self, client, hostile):
        returned = client.get("/health", headers={"X-Request-ID": hostile}).headers["X-Request-ID"]
        assert returned != hostile and re.fullmatch(r"[0-9a-f]{32}", returned)

    def test_error_responses_carry_a_request_id_too(self, client):
        assert "X-Request-ID" in client.get(f"/api/files/{'0' * 32}/").headers

    def test_cors_headers(self, client):
        response = client.get("/health", headers={"Origin": "https://app.example.com"})
        assert response.headers["access-control-allow-origin"] == "*"
        assert "Location" in response.headers["access-control-expose-headers"]

    def test_dashboard_escapes_user_controlled_values(self, client):
        response = client.get("/")
        assert response.status_code == 200 and "text/html" in response.headers["content-type"]
        html = response.text
        assert "esc(" in html
        template_expressions = re.findall(r"\$\{([^}]*)\}", html)
        user_controlled = [e for e in template_expressions if re.search(r"\.(feature_id|filename|error_message)\b", e)]
        assert user_controlled, "expected the dashboard to render user-controlled values"
        assert all(e.strip().startswith(("esc(", "featureLabel(")) for e in user_controlled), user_controlled

    def test_docs_openapi_and_static_files(self, client):
        assert client.get("/docs").status_code == 200
        paths = client.get("/openapi.json").json()["paths"]
        assert {"/api/files/", "/api/files/{file_id}/", "/api/files/{file_id}/measurements/"} <= paths.keys()
        assert client.get("/sample_data/survey_sample.kml").status_code == 200
        assert client.get("/sample_data/../README.md").status_code in (404, 400)  # no traversal

    def test_concurrent_uploads_all_succeed(self, client):
        """The CPU-bound parsing runs in worker threads, so parallel uploads must not collide."""
        from concurrent.futures import ThreadPoolExecutor

        def one(_):
            return client.post(
                "/api/files/", files={"file": ("c.kml", kml(kml_polygon(), kml_line()), "text/xml")}
            ).json()

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(one, range(16)))
        assert all(r["status"] == "COMPLETED" and r["feature_count"] == 2 for r in results)
        assert len({r["id"] for r in results}) == 16

    def test_json_bodies_are_valid_json_for_every_status_code(self, client):
        for response in (client.get("/health"), client.get(f"/api/files/{'0' * 32}/"), client.get("/api/files/")):
            json.loads(response.text)


# ====================================================================== worldwide coverage
class TestGlobalCoverage:
    """Surveys are not only Indian: the metric CRS must follow the feature (mining regions, MENA, both hemispheres)."""

    @pytest.mark.parametrize(
        ("place", "lon", "lat", "epsg"),
        [
            ("Bengaluru, India", 77.59, 12.97, 32643),
            ("Dubai, UAE", 55.27, 25.20, 32640),
            ("Witwatersrand, South Africa (southern hemisphere)", 28.05, -26.20, 32735),
            ("Pilbara, Australia (southern hemisphere)", 118.60, -22.30, 32750),
            ("Chuquicamata, Chile (western hemisphere)", -68.90, -22.30, 32719),
            ("Bergen, Norway (zone 32V exception)", 5.32, 60.39, 32632),
        ],
    )
    def test_local_utm_zone_is_chosen_and_matches_the_ellipsoid(self, client, upload, place, lon, lat, epsg):
        created = upload("site.kml", kml(kml_polygon(place, x=lon, y=lat, size=0.01)))
        assert created.status_code == 201
        feature = measurements(client, created.json()["id"])["features"][0]

        assert feature["status"] == "SUCCESS", place
        assert feature["projected_crs"] == f"EPSG:{epsg}", place
        expected = geodesic_polygon_area(square(lon, lat, 0.01))
        assert feature["measurement"]["value"] == pytest.approx(expected, rel=0.004), place
        assert feature["notes"] == []  # distortion stays below the warning threshold
