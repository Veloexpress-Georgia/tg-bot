"""Telegram signatures and short-lived, purpose-bound browser sessions."""

import base64
import hashlib
import hmac
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl

INIT_DATA_MAX_AGE = 600


class AuthError(ValueError):
    pass


@dataclass(frozen=True)
class InitData:
    user: dict[str, Any]
    # Identifies the signed assertion however its fields are ordered or encoded.
    fingerprint: str = field(repr=False)
    expires_at: int


def validate_init_data(
    raw: str, token: str, *, now: int | None = None, max_age: int = INIT_DATA_MAX_AGE
) -> dict[str, Any]:
    return read_init_data(raw, token, now=now, max_age=max_age).user


def read_init_data(
    raw: str, token: str, *, now: int | None = None, max_age: int = INIT_DATA_MAX_AGE
) -> InitData:
    """Validate Mini App launch data. Never log `raw`: it is a bearer credential."""
    try:
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
        fields = dict(pairs)
        if len(pairs) != len(fields) or len(raw) > 16384 or not token:
            raise AuthError("Invalid Telegram data")
        supplied = fields.pop("hash")
        secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
        expected = hmac.new(
            secret,
            "\n".join(f"{k}={v}" for k, v in sorted(fields.items())).encode(),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(supplied, expected):
            raise AuthError("Invalid Telegram signature")
        timestamp = int(fields["auth_date"])
        clock = int(time.time()) if now is None else now
        if not 0 <= clock - timestamp <= max_age:
            raise AuthError("Telegram login expired; reopen the app")
        user = json.loads(fields["user"])
        if not isinstance(user, dict) or type(user.get("id")) is not int or user["id"] <= 0:
            raise AuthError("Invalid Telegram identity")
        return InitData(
            user=user,
            fingerprint=hashlib.sha256(supplied.encode()).hexdigest(),
            expires_at=timestamp + max_age,
        )
    except (ValueError, KeyError, TypeError) as error:
        raise AuthError("Invalid or expired Telegram login") from error


class SessionSigner:
    def __init__(self, secret: str) -> None:
        if len(secret) < 32:
            raise ValueError("WEB_SESSION_SECRET must contain at least 32 characters")
        self.secret = secret.encode()

    def sign(
        self, claims: dict[str, Any], *, purpose: str, now: int | None = None, ttl: int = 43200
    ) -> str:
        clock = int(time.time()) if now is None else now
        body = (
            base64.urlsafe_b64encode(
                json.dumps(
                    {**claims, "purpose": purpose, "exp": clock + ttl}, separators=(",", ":")
                ).encode()
            )
            .decode()
            .rstrip("=")
        )
        signature = hmac.new(self.secret, body.encode(), hashlib.sha256).hexdigest()
        return f"{body}.{signature}"

    def read(self, token: str, *, purpose: str, now: int | None = None) -> dict[str, Any]:
        try:
            if len(token) > 8192:
                raise AuthError("Invalid session")
            body, supplied = token.rsplit(".", 1)
            expected = hmac.new(self.secret, body.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(supplied, expected):
                raise AuthError("Invalid session signature")
            claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
            clock = int(time.time()) if now is None else now
            if claims["purpose"] != purpose or claims["exp"] <= clock:
                raise AuthError("Session expired")
            return claims
        except (ValueError, KeyError, TypeError) as error:
            raise AuthError("Invalid or expired session") from error


class AssertionLedger:
    """Signed login assertions already exchanged for a session, kept until they expire.

    In memory: the API runs as one instance, and a restart forgets only
    assertions that are at most ten minutes old.
    """

    def __init__(self, *, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._used: dict[str, float] = {}

    def claim(self, fingerprint: str, *, expires_at: float) -> bool:
        now = self._clock()
        for key in [k for k, until in self._used.items() if until <= now]:
            del self._used[key]
        if fingerprint in self._used:
            return False
        self._used[fingerprint] = max(expires_at, now) + 1
        return True

    def release(self, fingerprint: str) -> None:
        self._used.pop(fingerprint, None)
