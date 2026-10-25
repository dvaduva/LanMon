import re

_HEX = re.compile(r"[0-9a-fA-F]")


def normalize_mac(value: str) -> str:
    """Aduce un MAC la forma aa:bb:cc:dd:ee:ff. Acceptă :, -, . sau fără separatori."""
    digits = "".join(_HEX.findall(value))
    stripped = re.sub(r"[\s:.\-]", "", value)
    if len(digits) != 12 or len(stripped) != 12:
        raise ValueError(f"Adresă MAC invalidă: {value!r}")
    digits = digits.lower()
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2))


def is_random_mac(mac: str) -> bool:
    """MAC administrat local (bitul 2 al primului octet), tipic pentru MAC-uri randomizate."""
    first_octet = int(normalize_mac(mac)[:2], 16)
    return bool(first_octet & 0x02)
