import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path, secret_key="test", version="test", background_tasks=False)


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as c:
        yield c


@pytest.fixture
def session_factory(settings):
    from app.db.migrate import upgrade_to_head
    from app.db.session import make_engine, make_session_factory

    upgrade_to_head(settings.database_url)
    engine = make_engine(settings.database_url)
    yield make_session_factory(engine)
    engine.dispose()
