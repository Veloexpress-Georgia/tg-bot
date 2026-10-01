import hashlib
import hmac
import json
from urllib.parse import urlencode

import pytest

from veloexpress_api.auth import AuthError, SessionSigner, validate_init_data


def signed_data(token: str, **changes: str) -> str:
    fields = {"auth_date": "1000", "user": json.dumps({"id": 42, "first_name": "Alice"})}
    fields.update(changes)
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(
        secret, "\n".join(f"{k}={v}" for k, v in sorted(fields.items())).encode(), hashlib.sha256
    ).hexdigest()
    return urlencode(fields)


def test_miniapp_auth_checks_signature_age_and_user() -> None:
    data = signed_data("test-token")
    assert validate_init_data(data, "test-token", now=1100)["id"] == 42
    for bad, token, now in (
        (data, "other-token", 1100),
        (data, "test-token", 2000),
        (data, "test-token", 900),
        (data + "&auth_date=1000", "test-token", 1100),
        (signed_data("test-token", user='{"id":true}'), "test-token", 1100),
    ):
        with pytest.raises(AuthError):
            validate_init_data(bad, token, now=now)


def test_session_tampering_expiry_and_purpose() -> None:
    signer = SessionSigner("x" * 40)
    token = signer.sign({"uid": 42, "csrf": "abc"}, purpose="session", now=1000, ttl=300)
    assert signer.read(token, purpose="session", now=1001)["uid"] == 42
    for bad, purpose, now in (
        (token + "a", "session", 1001),
        (token, "oauth", 1001),
        (token, "session", 1300),
    ):
        with pytest.raises(AuthError):
            signer.read(bad, purpose=purpose, now=now)
