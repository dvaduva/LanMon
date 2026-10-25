import socket

from app.services.wol import build_magic_packet, send_magic_packet


def test_magic_packet_structure():
    packet = build_magic_packet("AA-BB-CC-DD-EE-FF")
    mac = bytes.fromhex("aabbccddeeff")
    assert len(packet) == 102
    assert packet[:6] == b"\xff" * 6
    assert packet[6:] == mac * 16


def test_send_magic_packet_reaches_listener():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.settimeout(2)
        port = listener.getsockname()[1]
        sent = send_magic_packet("aa:bb:cc:dd:ee:ff", ["127.0.0.1", "127.0.0.1"], [port])
        assert sent == 1  # adresele duplicate se trimit o singură dată
        data, _ = listener.recvfrom(1024)
        assert data == build_magic_packet("aa:bb:cc:dd:ee:ff")
