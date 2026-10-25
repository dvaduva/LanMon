import socket
from collections.abc import Iterable

from app.mac import normalize_mac

WOL_PORTS = (9, 7)


def build_magic_packet(mac: str) -> bytes:
    """6 × 0xFF urmat de MAC repetat de 16 ori (102 octeți)."""
    mac_bytes = bytes.fromhex(normalize_mac(mac).replace(":", ""))
    return b"\xff" * 6 + mac_bytes * 16


def send_magic_packet(
    mac: str,
    broadcasts: Iterable[str] = ("255.255.255.255",),
    ports: Iterable[int] = WOL_PORTS,
    source_ip: str | None = None,
) -> int:
    """Trimite pachetul magic la fiecare adresă de broadcast și port. Întoarce nr. de pachete."""
    packet = build_magic_packet(mac)
    sent = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        if source_ip:
            # Pe QNAP cu mai multe interfețe, fixează placa de rețea pe care pleacă pachetul.
            sock.bind((source_ip, 0))
        for address in dict.fromkeys(broadcasts):
            for port in ports:
                sock.sendto(packet, (address, port))
                sent += 1
    return sent
