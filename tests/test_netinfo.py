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


def test_pick_network_falls_back_to_interface_netmask():
    # QTS keeps the subnet route in a per-interface table: only the default route is visible.
    routes = [(0, 0, "192.168.1.1", "br0", "192.168.1.5", 0)]
    with pytest.raises(RuntimeError):
        pick_network(routes)
    info = pick_network(routes, netmask=lambda iface: "255.255.255.0" if iface == "br0" else None)
    assert info.interface == "br0"
    assert str(info.address) == "192.168.1.5/24"
    assert info.broadcast == "192.168.1.255"


def test_interface_netmask_reads_the_kernel():
    from app.services.netinfo import interface_netmask

    assert interface_netmask("lo") in ("255.0.0.0", None)
    assert interface_netmask("no-such-iface0") is None
