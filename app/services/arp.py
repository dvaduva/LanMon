import ipaddress
from dataclasses import dataclass

from app.mac import normalize_mac


class ArpUnavailable(RuntimeError):
    """Sistemul nu permite trimiterea de pachete ARP (lipsește Npcap sau drepturile necesare)."""


NO_PCAP = (
    "Scanarea ARP nu e disponibilă: pe Windows trebuie instalat Npcap (npcap.com), "
    "cu opțiunea „WinPcap API-compatible mode”."
)
NO_PERMISSION = (
    "Scanarea ARP nu are drepturi pentru socket raw: rulează ca Administrator "
    "(Windows) sau cu capabilitatea NET_RAW (container)."
)


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
    try:
        answered, _ = srp(request, iface=interface, timeout=timeout, verbose=0)
    except PermissionError as exc:
        raise ArpUnavailable(NO_PERMISSION) from exc
    except RuntimeError as exc:
        if "layer 2" in str(exc) or "pcap" in str(exc).lower():
            raise ArpUnavailable(NO_PCAP) from exc
        raise
    replies = {
        rcv.hwsrc: ArpReply(ip=rcv.psrc, mac=normalize_mac(rcv.hwsrc)) for _, rcv in answered
    }
    return sorted(replies.values(), key=lambda r: ipaddress.IPv4Address(r.ip))
