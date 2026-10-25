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


def get_str(session: Session, key: str) -> str | None:
    setting = session.get(Setting, key)
    return setting.value if setting and setting.value else None


def set_value(session: Session, key: str, value: str | None) -> None:
    setting = session.get(Setting, key)
    if value is None:
        if setting is not None:
            session.delete(setting)
    elif setting is None:
        session.add(Setting(key=key, value=value))
    else:
        setting.value = value
