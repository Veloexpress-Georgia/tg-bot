"""Telegram signatures and short-lived, purpose-bound browser sessions."""

import base64
import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import parse_qsl


class AuthError(ValueError):
    pass


def validate_init_data(
    raw: str, token: str, *, now: int | None = None, max_age: int = 600
) -> dict[str, Any]:
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
        return user
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
