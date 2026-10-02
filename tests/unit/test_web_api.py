from datetime import date
from uuid import uuid4

import httpx
from tests.unit.test_payments_service import SharedDatabase, settings

from veloexpress_api.app import create_app
from veloexpress_api.auth import SessionSigner
from veloexpress_api.settings import WebSettings

SECRET = "s" * 40


def cookie(uid: int) -> str:
    return SessionSigner(SECRET).sign(
        {"uid": uid, "name": "Alice", "csrf": "csrf"}, purpose="session"
    )


async def test_api_access_control_csrf_and_command_retry() -> None:
    db = SharedDatabase()
    await db.create()
    config = settings().model_copy(
        update={"telegram_admin_ids": (42,), "telegram_bot_token": "123456:test-token"}
    )
    app = create_app(
        settings=config,
        web_settings=WebSettings(
            session_secret=SECRET,
            public_url="http://localhost:5173",
            telegram_client_id="",
            telegram_client_secret="",
        ),
        session_factory=db.session,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:5173"
    ) as client:
        assert (await client.get("/api/admin/days")).status_code == 401
        client.cookies.set("veloexpress_session", cookie(99))
        assert (await client.get("/api/admin/days")).status_code == 403
        assert (await client.get("/api/my-days")).status_code == 200
        body = {
            "command": {
                "request_id": str(uuid4()),
                "action": "manual",
                "service_date": date(2026, 10, 3).isoformat(),
                "lift_time": "8:30",
                "delta": 1,
            }
        }
        assert (
            await client.post("/api/commands", json=body, headers={"X-CSRF-Token": "csrf"})
        ).status_code == 403
        client.cookies.set("veloexpress_session", cookie(42))
        assert (await client.post("/api/commands", json=body)).status_code == 403
        assert (
            await client.post(
                "/api/commands",
                json=body,
                headers={"X-CSRF-Token": "csrf", "Origin": "https://evil.example"},
            )
        ).status_code == 403
        response = await client.post("/api/commands", json=body, headers={"X-CSRF-Token": "csrf"})
        assert response.status_code == 202
        assert response.json()["status"] == "pending"
        repeated = await client.post("/api/commands", json=body, headers={"X-CSRF-Token": "csrf"})
        assert repeated.json()["id"] == response.json()["id"]
        client.cookies.set("veloexpress_session", cookie(99))
        assert (await client.get(f"/api/commands/{response.json()['id']}")).status_code == 404
    await db.dispose()


async def test_admin_day_includes_only_its_scoped_commands() -> None:
    from tests.unit.test_payments_service import _fill, _setup

    from veloexpress_core.commands import CommandInput, CommandQueue

    db = SharedDatabase()
    await db.create()
    polls, _payments, _client, poll_id, day = await _setup(db)
    await _fill(polls, poll_id, 0, riders=6)
    config = settings().model_copy(
        update={"telegram_admin_ids": (42,), "telegram_bot_token": "123456:test-token"}
    )
    queue = CommandQueue(settings=config, session_factory=db.session)
    command = await queue.enqueue(
        CommandInput(
            request_id=uuid4(), action="manual", service_date=day, lift_time="8:30", delta=1
        ),
        actor_user_id=42,
    )
    await queue.enqueue(
        CommandInput(
            request_id=uuid4(),
            action="manual",
            service_date=date(2027, 1, 1),
            lift_time="8:30",
            delta=1,
        ),
        actor_user_id=42,
    )
    app = create_app(
        settings=config,
        web_settings=WebSettings(session_secret=SECRET, public_url="http://localhost:5173"),
        session_factory=db.session,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:5173"
    ) as client:
        client.cookies.set("veloexpress_session", cookie(42))
        response = await client.get(f"/api/admin/days/{day}")
        assert response.status_code == 200
        result = response.json()
        assert result["historical"] is False
        assert [c["id"] for c in result["commands"]] == [command["id"]]
        assert result["riders"]
        client.cookies.set("veloexpress_session", cookie(99))
        assert (await client.get(f"/api/admin/days/{day}")).status_code == 403
    await db.dispose()


async def test_sensitive_actions_require_confirmation_and_login_state_is_checked() -> None:
    db = SharedDatabase()
    await db.create()
    config = settings().model_copy(
        update={"telegram_admin_ids": (42,), "telegram_bot_token": "123456:test-token"}
    )
    app = create_app(
        settings=config,
        web_settings=WebSettings(
            session_secret=SECRET,
            public_url="http://localhost:5173",
            telegram_client_id="",
            telegram_client_secret="",
        ),
        session_factory=db.session,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:5173"
    ) as client:
        client.cookies.set("veloexpress_session", cookie(42))
        response = await client.post(
            "/api/commands",
            json={
                "command": {
                    "request_id": str(uuid4()),
                    "action": "cancel_day",
                    "service_date": "2026-10-03",
                }
            },
            headers={"X-CSRF-Token": "csrf"},
        )
        assert response.status_code == 409
        assert (await client.get("/api/auth/callback?code=forged&state=forged")).status_code == 401
        assert (await client.get("/api/auth/login")).status_code == 503
        assert (
            await client.post("/api/auth/telegram", json={"init_data": "forged"})
        ).status_code == 401
    await db.dispose()


async def test_confirmed_transport_retry_returns_original_even_without_new_confirmation() -> None:
    from veloexpress_core.commands import CommandInput, CommandQueue

    db = SharedDatabase()
    await db.create()
    config = settings().model_copy(
        update={"telegram_admin_ids": (42,), "telegram_bot_token": "123456:test-token"}
    )
    web = WebSettings(
        session_secret=SECRET,
        public_url="http://localhost:5173",
        telegram_client_id="",
        telegram_client_secret="",
    )
    spec = CommandInput(
        request_id=uuid4(), action="post", expected_digest="original-authorized-digest"
    )
    command = await CommandQueue(settings=config, session_factory=db.session).enqueue(
        spec, actor_user_id=42
    )
    app = create_app(settings=config, web_settings=web, session_factory=db.session)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=web.public_url
    ) as client:
        client.cookies.set("veloexpress_session", cookie(42))
        response = await client.post(
            "/api/commands",
            json={"command": spec.model_dump(mode="json", exclude={"expected_digest"})},
            headers={"X-CSRF-Token": "csrf"},
        )
        assert response.status_code == 202
        assert response.json()["id"] == command["id"]
    await db.dispose()


async def test_browser_oidc_checks_pkce_nonce_and_uses_bot_api_id(monkeypatch) -> None:
    import json
    import time
    from urllib.parse import parse_qs

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jwt.algorithms import RSAAlgorithm

    db = SharedDatabase()
    await db.create()
    config = settings().model_copy(
        update={"telegram_admin_ids": (42,), "telegram_bot_token": "123456:test-token"}
    )
    web = WebSettings(
        session_secret=SECRET,
        public_url="http://localhost:5173",
        telegram_client_id="123456",
        telegram_client_secret="client-secret",
    )
    app = create_app(settings=config, web_settings=web, session_factory=db.session)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": "test-key", "alg": "RS256"})
    original_client = httpx.AsyncClient
    async with original_client(
        transport=httpx.ASGITransport(app=app), base_url=web.public_url
    ) as client:
        login = await client.get("/api/auth/login")
        assert login.status_code == 302
        query = parse_qs(httpx.URL(login.headers["location"]).query.decode())
        assert query["code_challenge_method"] == ["S256"]
        flow = SessionSigner(SECRET).read(client.cookies["veloexpress_oauth"], purpose="oauth")
        token = jwt.encode(
            {
                "iss": "https://oauth.telegram.org",
                "aud": "123456",
                "sub": "999999999",
                "id": 42,
                "name": "Alice",
                "nonce": flow["nonce"],
                "iat": int(time.time()),
                "exp": int(time.time()) + 60,
            },
            private_key,
            algorithm="RS256",
            headers={"kid": "test-key"},
        )

        def exchange(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/token":
                payload = parse_qs(request.content.decode())
                assert payload["code_verifier"] == [flow["verifier"]]
                assert request.headers["Authorization"].startswith("Basic ")
                return httpx.Response(200, json={"id_token": token})
            return httpx.Response(200, json={"keys": [jwk]})

        transport = httpx.MockTransport(exchange)
        monkeypatch.setattr(
            httpx, "AsyncClient", lambda **kwargs: original_client(transport=transport, **kwargs)
        )
        response = await client.get(
            "/api/auth/callback", params={"code": "ok", "state": flow["state"]}
        )
        assert response.status_code == 303
        session = await client.get("/api/session")
        assert session.json()["user_id"] == 42 and session.json()["admin"] is True
        assert "HttpOnly" in response.headers["set-cookie"]
    await db.dispose()
