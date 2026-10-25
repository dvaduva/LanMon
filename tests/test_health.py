from sqlalchemy import inspect, text


def test_health_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok", "version": "test"}


def test_migrations_create_schema_and_seed(client, settings):
    assert settings.db_path.exists()
    engine = client.app.state.engine
    tables = set(inspect(engine).get_table_names())
    expected = {
        "devices",
        "discovered",
        "rooms",
        "device_types",
        "status_events",
        "actions_log",
        "settings",
        "users",
    }
    assert expected <= tables
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar() == "wal"
        assert (
            conn.execute(text("SELECT count(*) FROM device_types WHERE is_builtin")).scalar() == 10
        )
        interval = conn.execute(text("SELECT value FROM settings WHERE key='ping_interval_s'"))
        assert interval.scalar() == "30"


def test_restart_is_idempotent(settings):
    from fastapi.testclient import TestClient

    from app.main import create_app

    for _ in range(2):
        with TestClient(create_app(settings)) as c:
            assert c.get("/health").status_code == 200
