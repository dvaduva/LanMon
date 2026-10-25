import ipaddress
from dataclasses import dataclass

from app.mac import normalize_mac


@dataclass(frozen=True)
class ArpReply:
    ip: str
    mac: str


def arp_scan(network: str, interface: str | None = None, timeout: float = 3) -> list[ArpReply]:
    """Cerere ARP broadcast către tot subnetul; necesită NET_RAW.

    Se folosește srp fără filtru BPF (scapy potrivește singur răspunsurile), deci nu e nevoie
    de libpcap în imagine.
    """
    from scapy.layers.l2 import ARP, Ether
    from scapy.sendrecv import srp

    request = Ether(dst="ff:ff:ff:ff:ff:ff") / ARP(pdst=network)
    answered, _ = srp(request, iface=interface, timeout=timeout, verbose=0)
    replies = {
        rcv.hwsrc: ArpReply(ip=rcv.psrc, mac=normalize_mac(rcv.hwsrc)) for _, rcv in answered
    }
    return sorted(replies.values(), key=lambda r: ipaddress.IPv4Address(r.ip))
