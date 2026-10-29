from pathlib import Path

from app.mac import normalize_mac

ARP_COMPLETE = 0x2


def parse_arp_table(text: str) -> dict[str, str]:
    """/proc/net/arp contents → {mac: ip}, complete entries only."""
    table: dict[str, str] = {}
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        ip, _hw_type, flags, mac = parts[:4]
        if not int(flags, 16) & ARP_COMPLETE or mac == "00:00:00:00:00:00":
            continue
        table[normalize_mac(mac)] = ip
    return table


def read_arp_table(path: Path = Path("/proc/net/arp")) -> dict[str, str]:
    """The kernel's ARP table; with network_mode: host it is the QNAP's."""
    try:
        return parse_arp_table(path.read_text())
    except OSError:
        return {}
