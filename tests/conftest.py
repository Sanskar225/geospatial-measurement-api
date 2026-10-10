"""Test configuration.

Environment variables are set *before* any ``app`` module is imported so the suite runs
against a throw-away SQLite database and upload folder, never against real data.
"""

import os
import tempfile
from pathlib import Path

_TMP = Path(tempfile.mkdtemp(prefix="geoapi-tests-"))
os.environ["UPLOAD_DIR"] = str(_TMP / "uploads")
os.environ["DATA_DIR"] = str(_TMP / "data")
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP / 'test.db'}"
os.environ["LOG_LEVEL"] = "WARNING"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import create_app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    with TestClient(create_app()) as test_client:  # `with` runs the lifespan (creates tables)
        yield test_client


@pytest.fixture
def upload(client):
    """upload(filename, content, **query) -> Response"""

    def _upload(filename: str, content: bytes, **params):
        return client.post(
            "/api/files/", files={"file": (filename, content, "application/octet-stream")}, params=params
        )

    return _upload
