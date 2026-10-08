import asyncio
import pytest
from app.core.database import init_db

@pytest.fixture(scope="session", autouse=True)
def initialize_test_database():
    """Initializes the database schema tables for tests."""
    asyncio.run(init_db())
