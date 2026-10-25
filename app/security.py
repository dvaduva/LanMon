import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()

MIN_PASSWORD_LENGTH = 8


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def tokens_match(expected: str | None, given: str | None) -> bool:
    return bool(expected and given) and secrets.compare_digest(expected, given)


@dataclass
class _Attempts:
    failures: int = 0
    locked_until: float = 0.0


class LoginLimiter:
    """După `max_failures` încercări eșuate, adresa e blocată `lockout_s` secunde."""

    def __init__(
        self,
        max_failures: int = 5,
        lockout_s: float = 15 * 60,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_failures = max_failures
        self.lockout_s = lockout_s
        self._clock = clock
        self._attempts: dict[str, _Attempts] = {}

    def locked_for(self, key: str) -> float:
        """Secunde rămase din blocare (0 dacă nu e blocat)."""
        attempts = self._attempts.get(key)
        if attempts is None:
            return 0.0
        remaining = attempts.locked_until - self._clock()
        if remaining <= 0 and attempts.failures >= self.max_failures:
            del self._attempts[key]
            return 0.0
        return max(remaining, 0.0)

    def failure(self, key: str) -> None:
        attempts = self._attempts.setdefault(key, _Attempts())
        attempts.failures += 1
        if attempts.failures >= self.max_failures:
            attempts.locked_until = self._clock() + self.lockout_s

    def success(self, key: str) -> None:
        self._attempts.pop(key, None)
