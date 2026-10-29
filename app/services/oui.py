from functools import lru_cache

from netaddr import EUI, NotRegisteredError

from app.mac import is_random_mac, normalize_mac


@lru_cache(maxsize=4096)
def lookup_vendor(mac: str) -> str | None:
    """The vendor from the MAC's first 3 bytes (IEEE database bundled with netaddr)."""
    if is_random_mac(mac):
        return None
    try:
        return EUI(normalize_mac(mac)).oui.registration().org
    except (NotRegisteredError, IndexError):
        return None
