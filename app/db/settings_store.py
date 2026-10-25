from sqlalchemy.orm import Session

from app.db.models import Setting


def get_int(session: Session, key: str, default: int) -> int:
    setting = session.get(Setting, key)
    try:
        return int(setting.value) if setting and setting.value else default
    except ValueError:
        return default


def get_float(session: Session, key: str, default: float) -> float:
    setting = session.get(Setting, key)
    try:
        return float(setting.value) if setting and setting.value else default
    except ValueError:
        return default
