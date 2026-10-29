import socket
from collections.abc import Iterable

from app.mac import normalize_mac

WOL_PORTS = (9, 7)


def build_magic_packet(mac: str) -> bytes:
    """6 × 0xFF followed by the MAC repeated 16 times (102 bytes)."""
    mac_bytes = bytes.fromhex(normalize_mac(mac).replace(":", ""))
    return b"\xff" * 6 + mac_bytes * 16


def send_magic_packet(
    mac: str,
    broadcasts: Iterable[str] = ("255.255.255.255",),
    ports: Iterable[int] = WOL_PORTS,
    source_ip: str | None = None,
) -> int:
    """Sends the magic packet to every broadcast address and port. Returns the packet count."""
    packet = build_magic_packet(mac)
    sent = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if source_ip:
            # On a QNAP with several interfaces, pins the network card the packet leaves from.
            sock.bind((source_ip, 0))
        for address in dict.fromkeys(broadcasts):
            for port in ports:
                sock.sendto(packet, (address, port))
                sent += 1
    return sent
