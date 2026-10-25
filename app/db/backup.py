import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine

from app.db.migrate import alembic_config

log = logging.getLogger(__name__)

KEEP_PRE_MIGRATION = 5


def backup_database(db_path: Path, dest: Path) -> Path:
    """Snapshot consistent prin API-ul de backup SQLite (sigur cu WAL și scrieri în curs)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(db_path)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return dest


def pending_revision(url: str) -> tuple[str | None, str] | None:
    """(revizia curentă, head) dacă baza are nevoie de migrare, altfel None."""
    head = ScriptDirectory.from_config(alembic_config(url)).get_current_head()
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            current = MigrationContext.configure(conn).get_current_revision()
    finally:
        engine.dispose()
    return None if current == head else (current, head)


def backup_before_migration(db_path: Path, url: str, backups_dir: Path) -> Path | None:
    if not db_path.exists():
        return None
    pending = pending_revision(url)
    if pending is None or pending[0] is None:
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = backup_database(db_path, backups_dir / f"pre-migrare-{pending[0]}-{stamp}.db")
    log.info("Backup înainte de migrarea %s → %s: %s", pending[0], pending[1], dest)
    old = sorted(backups_dir.glob("pre-migrare-*.db"))[:-KEEP_PRE_MIGRATION]
    for path in old:
        path.unlink(missing_ok=True)
    return dest
