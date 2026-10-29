"""Phase 0 validation: ARP, ping and WOL from inside the container.

Run on the QNAP:
    docker exec -it lanmon python -m app.tools.netcheck all \
        --ping 192.168.1.10 --wol aa:bb:cc:dd:ee:ff --wait-ip 192.168.1.10
"""

import argparse
import sys
import time
from collections.abc import Callable

from app.config import get_settings
from app.mac import normalize_mac
from app.services.arp import arp_scan
from app.services.netinfo import NetInfo, resolve_network
from app.services.wol import WOL_PORTS, send_magic_packet


def network_from_args(args: argparse.Namespace) -> NetInfo:
    settings = get_settings()
    info = resolve_network(args.interface or settings.interface, args.subnet or settings.subnet)
    print(f"Interface: {info.interface}  IP: {info.address}  broadcast: {info.broadcast}")
    return info


def check_arp(info: NetInfo, timeout: float) -> bool:
    print(f"\n[ARP] Scanning {info.network} ...")
    started = time.monotonic()
    replies = arp_scan(str(info.network), info.interface, timeout)
    for reply in replies:
        print(f"  {reply.ip:<16} {reply.mac}")
    print(f"[ARP] {len(replies)} devices in {time.monotonic() - started:.1f} s")
    return bool(replies)


def check_ping(ip: str) -> bool:
    from icmplib import ping

    print(f"\n[PING] {ip} ...")
    host = ping(ip, count=3, interval=0.5, timeout=1, privileged=True)
    if host.is_alive:
        print(
            f"[PING] OK, {host.packets_received}/{host.packets_sent} replies, "
            f"average rtt {host.avg_rtt:.1f} ms"
        )
    else:
        print("[PING] No reply (on Windows, check the ICMPv4 Echo Request firewall rule)")
    return host.is_alive


def check_wol(info: NetInfo, mac: str, wait_ip: str | None, wait_s: int) -> bool:
    mac = normalize_mac(mac)
    broadcasts = [info.broadcast, "255.255.255.255"]
    sent = send_magic_packet(mac, broadcasts, WOL_PORTS, source_ip=str(info.address.ip))
    print(f"\n[WOL] {sent} packets sent to {mac} ({', '.join(broadcasts)}; ports 9, 7)")
    if not wait_ip:
        print("[WOL] Check by hand whether the PC starts (or use --wait-ip).")
        return True

    from icmplib import ping

    print(f"[WOL] Waiting for a ping reply from {wait_ip}, at most {wait_s} s ...")
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if ping(wait_ip, count=1, timeout=1, privileged=True).is_alive:
            print("[WOL] It's up!")
            return True
        time.sleep(5)
    print('[WOL] No reply. Check WOL in the BIOS/UEFI, "Wake on Magic Packet", Fast Startup.')
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="netcheck", description=__doc__)
    parser.add_argument("--interface", help="network interface (default: the default route's)")
    parser.add_argument("--subnet", help="subnet to scan, e.g. 192.168.1.0/24")
    sub = parser.add_subparsers(dest="command", required=True)

    p_arp = sub.add_parser("arp", help="ARP scan of the subnet")
    p_arp.add_argument("--timeout", type=float, default=3)

    p_ping = sub.add_parser("ping", help="ICMP ping over a raw socket")
    p_ping.add_argument("ip")

    p_wol = sub.add_parser("wol", help="send a magic packet")
    p_wol.add_argument("mac")
    p_wol.add_argument("--wait-ip", help="the PC's IP, to check it by ping")
    p_wol.add_argument("--wait", type=int, default=180)

    p_all = sub.add_parser("all", help="all checks")
    p_all.add_argument("--timeout", type=float, default=3)
    p_all.add_argument("--ping", dest="ping_ip", help="IP for the ping test")
    p_all.add_argument("--wol", dest="wol_mac", help="MAC for the WOL test")
    p_all.add_argument("--wait-ip", help="IP of the PC woken by WOL")
    p_all.add_argument("--wait", type=int, default=180)

    args = parser.parse_args(argv)
    info = network_from_args(args)

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
        except Exception as exc:  # one failed check doesn't stop the others
            print(f"[{name}] Error: {type(exc).__name__}: {exc}")
            results[name] = False

    print("\nResult: " + ", ".join(f"{k} {'OK' if v else 'FAILED'}" for k, v in results.items()))
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
