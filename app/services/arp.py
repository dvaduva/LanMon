import ipaddress
from dataclasses import dataclass

from app.i18n import N_
from app.mac import normalize_mac


class ArpUnavailable(RuntimeError):
    """The system can't send ARP packets (Npcap or the needed privileges are missing)."""


NO_PCAP = N_(
    "ARP scanning isn't available: on Windows, install Npcap (npcap.com) "
    "with the “WinPcap API-compatible mode” option."
)
NO_PERMISSION = N_(
    "ARP scanning has no permission for raw sockets: run as Administrator "
    "(Windows) or with the NET_RAW capability (container)."
)


@dataclass(frozen=True)
class ArpReply:
    ip: str
    mac: str


def arp_scan(network: str, interface: str | None = None, timeout: float = 3) -> list[ArpReply]:
    """Broadcast ARP request to the whole subnet; needs NET_RAW.

    Uses srp without a BPF filter (scapy matches the replies itself), so libpcap isn't
    needed in the image.
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
