import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path, secret_key="test", version="test")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c
