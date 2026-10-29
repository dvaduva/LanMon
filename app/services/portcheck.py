import asyncio
import contextlib


def parse_ports(text: str | None) -> list[int]:
    """ "3389, 22" → [22, 3389]; ValueError(the bad part) for invalid values."""
    ports = set()
    for part in (text or "").replace(";", ",").replace(" ", ",").split(","):
        if not part:
            continue
        if not part.isdigit() or not 0 < int(part) < 65536:
            raise ValueError(part)
        ports.add(int(part))
    return sorted(ports)


def format_ports(ports: list[int]) -> str | None:
    return ", ".join(str(p) for p in ports) or None


async def tcp_open(ip: str, port: int, timeout: float = 2) -> bool:
    try:
        _, writer = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout)
    except (OSError, TimeoutError):
        return False
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()
    return True


async def closed_ports(ip: str, ports: list[int], probe=tcp_open) -> list[int]:
    results = await asyncio.gather(*(probe(ip, p) for p in ports))
    return [p for p, ok in zip(ports, results, strict=True) if not ok]
