import ast
import string
from pathlib import Path

import pytest
from jinja2 import Environment
from jinja2.ext import extract_from_ast

from app.i18n import catalog, detail_text, gettext, negotiate, ngettext, plural_index
from tests.test_web import post

APP = Path(__file__).resolve().parent.parent / "app"

# Function name → index of the message argument.
PY_FUNCTIONS = {"t": 1, "gettext": 1, "tn": 1, "ngettext": 1, "N_": 0}


def _python_messages() -> set[str]:
    found = set()
    for path in APP.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            index = PY_FUNCTIONS.get(name)
            if index is None or len(node.args) <= index:
                continue
            arg = node.args[index]
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                found.add(arg.value)
    return found


def _template_messages() -> set[str]:
    env = Environment()
    found = set()
    for path in (APP / "web" / "templates").glob("*.html"):
        tree = env.parse(path.read_text(encoding="utf-8"))
        for _, _, message in extract_from_ast(tree, gettext_functions=("_", "ngettext")):
            first = message[0] if isinstance(message, tuple) else message
            if isinstance(first, str):
                found.add(first)
    return found


def _placeholders(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def test_romanian_catalog_covers_every_message():
    used = _python_messages() | _template_messages()
    ro = catalog("ro")
    assert sorted(used - ro.keys()) == [], "missing from app/locales/ro.json"
    assert sorted(ro.keys() - used) == [], "unused in app/locales/ro.json"


def test_romanian_translations_keep_placeholders():
    for message, translation in catalog("ro").items():
        forms = translation if isinstance(translation, list) else [translation]
        if isinstance(translation, list):
            assert len(forms) == 3, message
        for form in forms:
            assert _placeholders(form) <= _placeholders(message) | {"n"}, message


def test_gettext_and_plurals():
    assert gettext("ro", "Settings") == "Setări"
    assert gettext("en", "Settings") == "Settings"
    assert gettext("ro", "Wake {name}", name="PC") == "Pornește PC"
    one, many = "{n} device", "{n} devices"
    assert [ngettext("en", one, many, n) for n in (1, 2)] == ["1 device", "2 devices"]
    assert [ngettext("ro", one, many, n) for n in (1, 2, 20, 101)] == [
        "1 dispozitiv",
        "2 dispozitive",
        "20 de dispozitive",
        "101 dispozitive",
    ]
    assert [plural_index("ro", n) for n in (0, 1, 19, 20, 119, 120)] == [1, 0, 1, 2, 1, 2]


def test_detail_text_translates_known_prefix():
    assert detail_text("ro", "The command failed: sudo: password") == (
        "Comanda a eșuat: sudo: password"
    )
    assert detail_text("ro", "Something else") == "Something else"
    assert detail_text("en", "The command failed: x") == "The command failed: x"


@pytest.mark.parametrize(
    ("header", "expected"),
    [("ro-RO,ro;q=0.9,en;q=0.8", "ro"), ("de-DE,en;q=0.5", "en"), ("fr", "en"), (None, "en")],
)
def test_negotiate(header, expected):
    assert negotiate(header) == expected


def test_login_page_follows_browser_and_switcher(client):
    post(client, "/setup", {"username": "admin", "password": "parola-sigura", "confirm": "x"})
    page = client.get("/setup", headers={"Accept-Language": "ro"}).text
    assert "Bun venit în LanMon" in page and 'lang="ro"' in page
    assert "Welcome to LanMon" in client.get("/setup").text
    client.get("/language/ro", headers={"Referer": "http://testserver/setup"})
    assert "Bun venit în LanMon" in client.get("/setup").text


def test_language_is_per_user(admin):
    assert "Settings" in admin.get("/settings").text
    response = post(admin, "/settings/language", {"language": "ro"}, page="/settings")
    assert response.status_code == 303
    assert "Limba interfeței" in admin.get("/settings").text
    # Another user keeps their own language.
    post(
        admin,
        "/users/new",
        {"username": "ana", "password": "parola-ana", "role": "member", "language": "en"},
        page="/users/new",
    )
    post(admin, "/logout", page="/settings")
    assert "Autentificare" in admin.get("/login").text  # the sign-in page keeps the last language
    post(admin, "/login", {"username": "ana", "password": "parola-ana"}, page="/login")
    assert "Change password" in admin.get("/settings").text


def test_migration_keeps_existing_installs_in_romanian(settings):
    import sqlalchemy as sa
    from alembic import command

    from app.db.migrate import alembic_config, upgrade_to_head

    url = settings.database_url
    command.upgrade(alembic_config(url), "0007")
    engine = sa.create_engine(url)
    with engine.begin() as conn:
        conn.execute(sa.text("UPDATE device_types SET name = 'Imprimantă' WHERE name = 'Printer'"))
        conn.execute(
            sa.text(
                "INSERT INTO users (username, password_hash, role, created_at)"
                " VALUES ('dan', 'x', 'admin', CURRENT_TIMESTAMP)"
            )
        )
    upgrade_to_head(url)
    with engine.connect() as conn:
        assert conn.execute(sa.text("SELECT language FROM users")).scalar() == "ro"
        names = set(conn.execute(sa.text("SELECT name FROM device_types")).scalars())
    engine.dispose()
    assert "Printer" in names and "Imprimantă" not in names
