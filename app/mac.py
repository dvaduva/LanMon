import re

_HEX = re.compile(r"[0-9a-fA-F]")


def normalize_mac(value: str) -> str:
    """Normalizes a MAC to aa:bb:cc:dd:ee:ff. Accepts :, -, . or no separators."""
    digits = "".join(_HEX.findall(value))
    stripped = re.sub(r"[\s:.\-]", "", value)
    if len(digits) != 12 or len(stripped) != 12:
        raise ValueError(f"Invalid MAC address: {value!r}")
    digits = digits.lower()
    return ":".join(digits[i : i + 2] for i in range(0, 12, 2))


def is_random_mac(mac: str) -> bool:
    """Locally administered MAC (bit 2 of the first octet), typical for randomized MACs."""
    first_octet = int(normalize_mac(mac)[:2], 16)
    return bool(first_octet & 0x02)
