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
    """Alege interfața și subnetul LAN din tabela de rute scapy.

    Fiecare rută: (net, mask, gateway, iface, addr, metric). Fără `interface`, se folosește
    interfața rutei implicite.
    """
    if interface is None:
        defaults = [r for r in routes if r[0] == 0 and r[1] == 0]
        if not defaults:
            raise RuntimeError("Nu am găsit ruta implicită; setează LANMON_INTERFACE.")
        interface = min(defaults, key=lambda r: r[5])[3]

    for net, mask, gateway, iface, addr, _metric in routes:
        if iface != interface or mask == 0 or gateway != "0.0.0.0":
            continue
        if net == 0 or mask == 0xFFFFFFFF:
            continue
        address = ipaddress.IPv4Interface(f"{addr}/{_ltoa(mask)}")
        if address.network.network_address == ipaddress.IPv4Address(net):
            return NetInfo(interface=interface, address=address)
    raise RuntimeError(
        f"Nu am găsit subnetul pentru interfața {interface!r}; setează LANMON_SUBNET."
    )


def detect_network(interface: str | None = None) -> NetInfo:
    import scapy.route  # noqa: F401  (populează conf.route)
    from scapy.config import conf

    routes = [
        (net, mask, gw, iface if isinstance(iface, str) else iface.name, addr, metric)
        for net, mask, gw, iface, addr, metric in conf.route.routes
    ]
    return pick_network(routes, interface)
