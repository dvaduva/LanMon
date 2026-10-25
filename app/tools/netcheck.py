"""Validare Faza 0: ARP, ping și WOL din container.

Rulare pe QNAP:
    docker exec -it lanmon python -m app.tools.netcheck all \
        --ping 192.168.1.10 --wol aa:bb:cc:dd:ee:ff --wait-ip 192.168.1.10
"""

import argparse
import ipaddress
import sys
import time
from collections.abc import Callable

from app.config import get_settings
from app.mac import normalize_mac
from app.services.arp import arp_scan
from app.services.netinfo import NetInfo, detect_network
from app.services.wol import WOL_PORTS, send_magic_packet


def resolve_network(args: argparse.Namespace) -> NetInfo:
    settings = get_settings()
    interface = args.interface or settings.interface
    info = detect_network(interface)
    subnet = args.subnet or settings.subnet
    if subnet:
        address = ipaddress.IPv4Interface(
            f"{info.address.ip}/{ipaddress.IPv4Network(subnet).prefixlen}"
        )
        info = NetInfo(interface=info.interface, address=address)
    print(f"Interfață: {info.interface}  IP: {info.address}  broadcast: {info.broadcast}")
    return info


def check_arp(info: NetInfo, timeout: float) -> bool:
    print(f"\n[ARP] Scanez {info.network} ...")
    started = time.monotonic()
    replies = arp_scan(str(info.network), info.interface, timeout)
    for reply in replies:
        print(f"  {reply.ip:<16} {reply.mac}")
    print(f"[ARP] {len(replies)} dispozitive în {time.monotonic() - started:.1f} s")
    return bool(replies)


def check_ping(ip: str) -> bool:
    from icmplib import ping

    print(f"\n[PING] {ip} ...")
    host = ping(ip, count=3, interval=0.5, timeout=1, privileged=True)
    if host.is_alive:
        print(
            f"[PING] OK, {host.packets_received}/{host.packets_sent} răspunsuri, "
            f"rtt mediu {host.avg_rtt:.1f} ms"
        )
    else:
        print("[PING] Fără răspuns (la Windows verifică regula de firewall ICMPv4 Echo Request)")
    return host.is_alive


def check_wol(info: NetInfo, mac: str, wait_ip: str | None, wait_s: int) -> bool:
    mac = normalize_mac(mac)
    broadcasts = [info.broadcast, "255.255.255.255"]
    sent = send_magic_packet(mac, broadcasts, WOL_PORTS, source_ip=str(info.address.ip))
    print(f"\n[WOL] {sent} pachete trimise către {mac} ({', '.join(broadcasts)}; porturi 9, 7)")
    if not wait_ip:
        print("[WOL] Verifică manual dacă PC-ul pornește (sau folosește --wait-ip).")
        return True

    from icmplib import ping

    print(f"[WOL] Aștept răspuns la ping de la {wait_ip}, maxim {wait_s} s ...")
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if ping(wait_ip, count=1, timeout=1, privileged=True).is_alive:
            print("[WOL] Pornit!")
            return True
        time.sleep(5)
    print("[WOL] Nu a răspuns. Verifică WOL în BIOS/UEFI, „Wake on Magic Packet”, Fast Startup.")
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="netcheck", description=__doc__)
    parser.add_argument("--interface", help="interfața de rețea (implicit: cea a rutei implicite)")
    parser.add_argument("--subnet", help="subnetul de scanat, ex. 192.168.1.0/24")
    sub = parser.add_subparsers(dest="command", required=True)

    p_arp = sub.add_parser("arp", help="scanare ARP pe subnet")
    p_arp.add_argument("--timeout", type=float, default=3)

    p_ping = sub.add_parser("ping", help="ping ICMP prin socket raw")
    p_ping.add_argument("ip")

    p_wol = sub.add_parser("wol", help="trimite pachet magic")
    p_wol.add_argument("mac")
    p_wol.add_argument("--wait-ip", help="IP-ul PC-ului, pentru verificare prin ping")
    p_wol.add_argument("--wait", type=int, default=180)

    p_all = sub.add_parser("all", help="toate verificările")
    p_all.add_argument("--timeout", type=float, default=3)
    p_all.add_argument("--ping", dest="ping_ip", help="IP pentru testul de ping")
    p_all.add_argument("--wol", dest="wol_mac", help="MAC pentru testul WOL")
    p_all.add_argument("--wait-ip", help="IP-ul PC-ului trezit prin WOL")
    p_all.add_argument("--wait", type=int, default=180)

    args = parser.parse_args(argv)
    info = resolve_network(args)

    checks: list[tuple[str, Callable[[], bool]]] = []
    if args.command in ("arp", "all"):
        checks.append(("ARP", lambda: check_arp(info, args.timeout)))
    if args.command == "ping":
        checks.append(("PING", lambda: check_ping(args.ip)))
    if args.command == "all" and args.ping_ip:
        checks.append(("PING", lambda: check_ping(args.ping_ip)))
    if args.command == "wol":
        checks.append(("WOL", lambda: check_wol(info, args.mac, args.wait_ip, args.wait)))
    if args.command == "all" and args.wol_mac:
        checks.append(("WOL", lambda: check_wol(info, args.wol_mac, args.wait_ip, args.wait)))

    results: dict[str, bool] = {}
    for name, check in checks:
        try:
            results[name] = check()
        except Exception as exc:  # o verificare căzută nu le oprește pe celelalte
            print(f"[{name}] Eroare: {type(exc).__name__}: {exc}")
            results[name] = False

    print("\nRezultat: " + ", ".join(f"{k} {'OK' if v else 'EȘUAT'}" for k, v in results.items()))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
