import ipaddress

import pytest

from app.services.netinfo import pick_network


def _ip(s: str) -> int:
    return int(ipaddress.IPv4Address(s))


ROUTES = [
    (0, 0, "192.168.1.1", "eth0", "192.168.1.20", 0),
    (_ip("192.168.1.0"), _ip("255.255.255.0"), "0.0.0.0", "eth0", "192.168.1.20", 0),
    (_ip("172.17.0.0"), _ip("255.255.0.0"), "0.0.0.0", "docker0", "172.17.0.1", 0),
    (_ip("127.0.0.0"), _ip("255.0.0.0"), "0.0.0.0", "lo", "127.0.0.1", 0),
]


def test_pick_network_uses_default_route_interface():
    info = pick_network(ROUTES)
    assert info.interface == "eth0"
    assert str(info.network) == "192.168.1.0/24"
    assert info.broadcast == "192.168.1.255"


def test_pick_network_with_explicit_interface():
    assert str(pick_network(ROUTES, "docker0").network) == "172.17.0.0/16"


def test_pick_network_without_default_route():
    with pytest.raises(RuntimeError):
        pick_network(ROUTES[1:])
