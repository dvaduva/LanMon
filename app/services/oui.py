from functools import lru_cache

from netaddr import EUI, NotRegisteredError

from app.mac import is_random_mac, normalize_mac


@lru_cache(maxsize=4096)
def lookup_vendor(mac: str) -> str | None:
    """Producătorul din primii 3 octeți ai MAC-ului (baza IEEE inclusă în netaddr)."""
    if is_random_mac(mac):
        return None
    try:
        return EUI(normalize_mac(mac)).oui.registration().org
    except (NotRegisteredError, IndexError):
        return None
