from sqlalchemy.orm import Session

from app.db.models import Setting


def get_int(session: Session, key: str, default: int) -> int:
    setting = session.get(Setting, key)
    try:
        return int(setting.value) if setting and setting.value else default
    except ValueError:
        return default
