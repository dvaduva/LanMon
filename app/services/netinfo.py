import ipaddress
from dataclasses import dataclass


@dataclass(frozen=True)
class NetInfo:
    interface: str
    address: ipaddress.IPv4Interface

    @property
    def network(self) -> ipaddress.IPv4Network:
        return self.address.network

    @property
    def broadcast(self) -> str:
        return str(self.network.broadcast_address)


def _ltoa(value: int) -> str:
    return str(ipaddress.IPv4Address(value))


def pick_network(routes: list[tuple], interface: str | None = None) -> NetInfo:
    """Picks the LAN interface and subnet from scapy's routing table.

    Each route: (net, mask, gateway, iface, addr, metric). Without `interface`, the default
    route's interface is used.
    """
    if interface is None:
        defaults = [r for r in routes if r[0] == 0 and r[1] == 0]
        if not defaults:
            raise RuntimeError("No default route found; set LANMON_INTERFACE.")
        interface = min(defaults, key=lambda r: r[5])[3]

    for net, mask, gateway, iface, addr, _metric in routes:
        if iface != interface or mask == 0 or gateway != "0.0.0.0":
            continue
        if net == 0 or mask == 0xFFFFFFFF:
            continue
        address = ipaddress.IPv4Interface(f"{addr}/{_ltoa(mask)}")
        if address.network.network_address == ipaddress.IPv4Address(net):
            return NetInfo(interface=interface, address=address)
    raise RuntimeError(f"No subnet found for interface {interface!r}; set LANMON_SUBNET.")


def detect_network(interface: str | None = None) -> NetInfo:
    import scapy.route  # noqa: F401  (populates conf.route)
    from scapy.config import conf

    routes = [
        (net, mask, gw, iface if isinstance(iface, str) else iface.name, addr, metric)
        for net, mask, gw, iface, addr, metric in conf.route.routes
    ]
    return pick_network(routes, interface)


def resolve_network(interface: str | None = None, subnet: str | None = None) -> NetInfo:
    """The interface from the routes (or the given one); a given subnet overrides the mask."""
    info = detect_network(interface)
    if subnet:
        prefix = ipaddress.IPv4Network(subnet, strict=False).prefixlen
        info = NetInfo(info.interface, ipaddress.IPv4Interface(f"{info.address.ip}/{prefix}"))
    return info
