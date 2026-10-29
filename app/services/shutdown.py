import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import asyncssh
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import ActionLog, Device

log = logging.getLogger(__name__)

OK, ERROR = "ok", "error"

OS_NAMES = {"windows": "Windows", "linux": "Linux", "macos": "macOS"}
COMMANDS = {
    "windows": "shutdown /s /t 0",
    "linux": "sudo -n shutdown -h now",
    "macos": "sudo -n shutdown -h now",
}
SSH_PORT = 22


def load_or_create_key(path: Path) -> asyncssh.SSHKey:
    """Cheia ed25519 a aplicației; se generează o singură dată în directorul de date."""
    if path.exists():
        return asyncssh.read_private_key(path)
    key = asyncssh.generate_private_key("ssh-ed25519", comment="lanmon")
    path.write_bytes(key.export_private_key())
    os.chmod(path, 0o600)
    return key


def key_fingerprint(key: asyncssh.SSHKey) -> str:
    """Tip + date, fără comentariu, ca amprentele să se compare exact."""
    return " ".join(key.export_public_key().decode().split()[:2])


@dataclass(frozen=True)
class ShutdownTarget:
    id: int
    name: str
    ip: str | None
    user: str | None
    os: str | None
    host_key: str | None


class _Client(asyncssh.SSHClient):
    """Trust on first use: prima amprentă se memorează, apoi trebuie să rămână aceeași."""

    def __init__(self, expected: str | None) -> None:
        self.expected = expected
        self.seen: str | None = None

    def validate_host_public_key(self, host, addr, port, key) -> bool:
        self.seen = key_fingerprint(key)
        return self.expected is None or self.seen == self.expected


class Shutdowner:
    """Oprește dispozitive prin SSH, cu cheia generată de aplicație."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        key_path: Path,
        port: int = SSH_PORT,
        timeout: float = 10,
    ) -> None:
        self._session_factory = session_factory
        self._key_path = key_path
        self._key: asyncssh.SSHKey | None = None
        self.port = port
        self.timeout = timeout
        self.pending: set[int] = set()

    @property
    def key(self) -> asyncssh.SSHKey:
        if self._key is None:
            self._key = load_or_create_key(self._key_path)
        return self._key

    def public_key(self) -> str:
        return self.key.export_public_key().decode().strip()

    async def shutdown(self, device_id: int, source: str = "user") -> tuple[bool, str]:
        """Trimite comanda de oprire; întoarce (reușit, mesaj pentru utilizator)."""
        target = await asyncio.to_thread(self._load, device_id)
        if target is None:
            return False, "Dispozitivul nu are oprirea de la distanță activată."
        if target.id in self.pending:
            return False, f"„{target.name}” se oprește deja."
        self.pending.add(target.id)
        try:
            ok, detail, seen = await self._run(target)
        finally:
            self.pending.discard(target.id)
        await asyncio.to_thread(self._record, target, source, ok, detail, seen)
        if ok:
            return True, f"Am trimis comanda de oprire către „{target.name}”."
        return False, f"Nu am putut opri „{target.name}”: {detail}"

    def _load(self, device_id: int) -> ShutdownTarget | None:
        with self._session_factory() as session:
            device = session.get(Device, device_id)
            if device is None or not device.shutdown_enabled:
                return None
            return ShutdownTarget(
                device.id,
                device.name,
                device.last_ip,
                device.ssh_user,
                device.os,
                device.ssh_host_key,
            )

    async def _run(self, target: ShutdownTarget) -> tuple[bool, str | None, str | None]:
        """(reușit, detaliu, amprenta văzută)."""
        if not target.ip:
            return False, "nu are o adresă IP cunoscută.", None
        if not target.user or target.os not in COMMANDS:
            return False, "lipsește utilizatorul SSH sau sistemul de operare.", None
        client = _Client(target.host_key)
        try:
            key = await asyncio.to_thread(lambda: self.key)
            async with asyncssh.connect(
                target.ip,
                port=self.port,
                username=target.user,
                client_keys=[key],
                client_factory=lambda: client,
                known_hosts=([], [], []),
                agent_path=None,
                config=[],
                preferred_auth="publickey",
                connect_timeout=self.timeout,
            ) as conn:
                try:
                    result = await conn.run(COMMANDS[target.os], check=False, timeout=self.timeout)
                except (asyncssh.ConnectionLost, asyncssh.DisconnectError):
                    # Sistemul a închis conexiunea pentru că se oprește.
                    return True, None, client.seen
                except TimeoutError:
                    return True, "comanda nu a răspuns; probabil se oprește.", client.seen
        except (asyncssh.HostKeyNotVerifiable, asyncssh.KeyExchangeFailed):
            if target.host_key and client.seen and client.seen != target.host_key:
                return (
                    False,
                    "amprenta SSH s-a schimbat față de cea memorată. Dacă sistemul a fost "
                    "reinstalat, șterge amprenta din fișa dispozitivului.",
                    None,
                )
            return False, "conexiunea SSH a fost refuzată la verificarea amprentei.", None
        except asyncssh.PermissionDenied:
            reason = "cheia aplicației nu e acceptată (vezi Setări → Oprire de la distanță)."
            return False, reason, None
        except TimeoutError:
            return False, f"nu răspunde pe portul SSH ({self.port}).", None
        except (OSError, asyncssh.Error) as exc:
            return False, f"conexiunea SSH a eșuat ({str(exc) or type(exc).__name__}).", None
        if result.exit_status in (0, None):
            return True, None, client.seen
        message = str(result.stderr or result.stdout or "").strip().splitlines()
        reason = message[0] if message else f"cod de ieșire {result.exit_status}"
        return False, f"comanda a eșuat: {reason}", client.seen

    def _record(
        self,
        target: ShutdownTarget,
        source: str,
        ok: bool,
        detail: str | None,
        seen: str | None,
    ) -> None:
        now = datetime.now(UTC)
        with self._session_factory.begin() as session:
            session.add(
                ActionLog(
                    device_id=target.id,
                    action="shutdown",
                    source=source,
                    result=OK if ok else ERROR,
                    detail=detail,
                    created_at=now,
                    finished_at=now,
                )
            )
            device = session.get(Device, target.id)
            if device is not None and seen and device.ssh_host_key is None:
                device.ssh_host_key = seen
        log.info("Oprire %s: %s %s", target.name, OK if ok else ERROR, detail or "")
