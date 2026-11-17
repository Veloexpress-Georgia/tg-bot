"""Cabinet access, rider command limits and private rider cards."""

import json
import logging
import time
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, Mock
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import GetChatMember
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from tests.unit.test_payments_service import SharedDatabase, settings
from tests.unit.test_web_api import SECRET, cookie
from tests.unit.test_web_auth import signed_data

from veloexpress_api.app import create_app
from veloexpress_api.limits import RateLimiter
from veloexpress_api.membership import (
    MembershipGate,
    MembershipUnavailable,
    telegram_member_lookup,
)
from veloexpress_api.settings import WebSettings
from veloexpress_bot.db.models import AdminCommand
from veloexpress_core.commands import (
    MAX_PENDING_RIDER_COMMANDS,
    CommandInput,
    CommandLimitError,
    CommandQueue,
    Operations,
)

TOKEN = "123456:test-token"
ORIGIN = "http://localhost:5173"


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Members:
    def __init__(self, *members: int) -> None:
        self.members = set(members)
        self.down = False
        self.calls: list[int] = []

    async def __call__(self, user_id: int) -> bool:
        self.calls.append(user_id)
        if self.down:
            raise MembershipUnavailable("Telegram is down")
        return user_id in self.members


def _config(**changes: Any):
    return settings().model_copy(
        update={"telegram_admin_ids": (1,), "telegram_bot_token": TOKEN, **changes}
    )


def _app(db: SharedDatabase, members: Members, clock: Clock | None = None, **kwargs: Any):
    return create_app(
        settings=kwargs.pop("config", None) or _config(),
        web_settings=kwargs.pop("web", None)
        or WebSettings(session_secret=SECRET, public_url=ORIGIN),
        session_factory=db.session,
        membership=MembershipGate(members, clock=clock or Clock()),
        **kwargs,
    )


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN)


def _init_data(user_id: int = 42) -> str:
    return signed_data(
        TOKEN,
        auth_date=str(int(time.time())),
        user=json.dumps({"id": user_id, "first_name": "Alice"}),
    )


# 1. Only current chat members hold a cabinet session.


async def test_miniapp_login_is_refused_to_a_non_member() -> None:
    db = SharedDatabase()
    await db.create()
    try:
        members = Members()
        async with _client(_app(db, members)) as client:
            response = await client.post("/api/auth/telegram", json={"init_data": _init_data()})
            assert response.status_code == 403
            assert response.headers["x-error-code"] == "not_member"
            assert "members" in response.json()["detail"]
            assert "veloexpress_session" not in response.cookies
            assert members.calls == [42]
            assert (await client.get("/api/session")).status_code == 401
    finally:
        await db.dispose()


async def test_a_session_is_revalidated_after_the_cache_expires() -> None:
    db = SharedDatabase()
    await db.create()
    try:
        members, clock = Members(42), Clock()
        async with _client(_app(db, members, clock)) as client:
            response = await client.post("/api/auth/telegram", json={"init_data": _init_data()})
            assert response.status_code == 200
            assert (await client.get("/api/session")).status_code == 200
            assert (await client.get("/api/my-days")).status_code == 200
            # Cached: page loads do not reach Telegram.
            assert members.calls == [42]
            members.members.clear()
            clock.now += 60
            assert (await client.get("/api/my-days")).status_code == 200
            clock.now += 600
            response = await client.get("/api/my-days")
            assert response.status_code == 403
            assert response.headers["x-error-code"] == "not_member"
            assert (await client.get("/api/session")).status_code == 403
            assert members.calls == [42, 42]
    finally:
        await db.dispose()


async def test_an_old_session_cookie_of_a_former_member_is_refused() -> None:
    db = SharedDatabase()
    await db.create()
    try:
        async with _client(_app(db, Members())) as client:
            client.cookies.set("veloexpress_session", cookie(99))
            assert (await client.get("/api/my-days")).status_code == 403
            assert (await client.get("/api/session")).status_code == 403
    finally:
        await db.dispose()


async def test_unreachable_telegram_blocks_login_and_expires_after_the_grace_period() -> None:
    db = SharedDatabase()
    await db.create()
    try:
        members, clock = Members(42), Clock()
        async with _client(_app(db, members, clock)) as client:
            members.down = True
            response = await client.post("/api/auth/telegram", json={"init_data": _init_data()})
            assert response.status_code == 503
            members.down = False
            assert (
                await client.post("/api/auth/telegram", json={"init_data": _init_data()})
            ).status_code == 200
            members.down = True
            clock.now += 700
            # Membership was confirmed recently, so an outage does not log riders out.
            assert (await client.get("/api/my-days")).status_code == 200
            calls = len(members.calls)
            assert (await client.get("/api/my-days")).status_code == 200
            assert len(members.calls) == calls
            clock.now += 3600
            assert (await client.get("/api/my-days")).status_code == 503
    finally:
        await db.dispose()


async def test_browser_login_of_a_non_member_returns_to_the_login_page(monkeypatch) -> None:
    from urllib.parse import parse_qs

    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa
    from jwt.algorithms import RSAAlgorithm

    from veloexpress_api.auth import SessionSigner

    db = SharedDatabase()
    await db.create()
    web = WebSettings(
        session_secret=SECRET,
        public_url=ORIGIN,
        telegram_client_id="123456",
        telegram_client_secret="client-secret",
    )
    app = _app(db, Members(), web=web)
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": "test-key", "alg": "RS256"})
    original_client = httpx.AsyncClient
    try:
        async with _client(app) as client:
            await client.get("/api/auth/login")
            flow = SessionSigner(SECRET).read(client.cookies["veloexpress_oauth"], purpose="oauth")
            token = jwt.encode(
                {
                    "iss": "https://oauth.telegram.org",
                    "aud": "123456",
                    "sub": "1",
                    "id": 77,
                    "name": "Stranger",
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
                    assert parse_qs(request.content.decode())["code_verifier"]
                    return httpx.Response(200, json={"id_token": token})
                return httpx.Response(200, json={"keys": [jwk]})

            transport = httpx.MockTransport(exchange)
            monkeypatch.setattr(
                httpx,
                "AsyncClient",
                lambda **kwargs: original_client(transport=transport, **kwargs),
            )
            response = await client.get(
                "/api/auth/callback", params={"code": "ok", "state": flow["state"]}
            )
            assert response.status_code == 303
            assert response.headers["location"] == "/?login_error=not_member"
            assert "veloexpress_session=" not in response.headers.get("set-cookie", "")
            assert (await client.get("/api/session")).status_code == 401
    finally:
        await db.dispose()


@pytest.mark.parametrize(
    "answer, expected",
    [
        (SimpleNamespace(status="creator"), True),
        (SimpleNamespace(status="administrator"), True),
        (SimpleNamespace(status="member"), True),
        (SimpleNamespace(status="restricted", is_member=True), True),
        (SimpleNamespace(status="restricted", is_member=False), False),
        (SimpleNamespace(status="left"), False),
        (SimpleNamespace(status="kicked"), False),
    ],
)
async def test_telegram_membership_statuses(answer: SimpleNamespace, expected: bool) -> None:
    bot = Mock()
    bot.get_chat_member = AsyncMock(return_value=answer)
    lookup = telegram_member_lookup(bot, -100123)
    assert await lookup(42) is expected
    assert bot.get_chat_member.await_args.kwargs["chat_id"] == -100123


async def test_telegram_membership_errors() -> None:
    method = GetChatMember(chat_id=-100123, user_id=42)
    bot = Mock()
    bot.get_chat_member = AsyncMock(
        side_effect=TelegramBadRequest(method=method, message="Bad Request: user not found")
    )
    assert await telegram_member_lookup(bot, -100123)(42) is False
    for error in (
        TelegramBadRequest(method=method, message="Bad Request: chat not found"),
        TelegramNetworkError(method=method, message="timeout"),
    ):
        bot.get_chat_member = AsyncMock(side_effect=error)
        with pytest.raises(MembershipUnavailable):
            await telegram_member_lookup(bot, -100123)(42)
    with pytest.raises(MembershipUnavailable):
        await telegram_member_lookup(bot, None)(42)


# 5. A Mini App assertion is exchanged for one session only.


async def test_init_data_cannot_be_replayed_from_another_client(caplog) -> None:
    db = SharedDatabase()
    await db.create()
    try:
        caplog.set_level(logging.DEBUG)
        app = _app(db, Members(42))
        data = _init_data()
        async with _client(app) as first, _client(app) as second:
            assert (
                await first.post("/api/auth/telegram", json={"init_data": data})
            ).status_code == 200
            # A Mini App reload in the same browser keeps working.
            again = await first.post("/api/auth/telegram", json={"init_data": data})
            assert again.status_code == 200
            assert (await first.get("/api/session")).json()["user_id"] == 42
            stolen = await second.post("/api/auth/telegram", json={"init_data": data})
            assert stolen.status_code == 401
            assert (await second.get("/api/session")).status_code == 401
        assert data not in caplog.text
        assert data.split("hash=")[-1] not in caplog.text
    finally:
        await db.dispose()


async def test_a_refused_exchange_does_not_consume_the_assertion() -> None:
    db = SharedDatabase()
    await db.create()
    try:
        members = Members(42)
        members.down = True
        data = _init_data()
        async with _client(_app(db, members)) as client:
            assert (
                await client.post("/api/auth/telegram", json={"init_data": data})
            ).status_code == 503
            members.down = False
            assert (
                await client.post("/api/auth/telegram", json={"init_data": data})
            ).status_code == 200
    finally:
        await db.dispose()


async def test_a_cookie_of_another_user_does_not_short_circuit_a_replay() -> None:
    db = SharedDatabase()
    await db.create()
    try:
        app = _app(db, Members(42, 43))
        data = _init_data(42)
        async with _client(app) as first, _client(app) as second:
            assert (
                await first.post("/api/auth/telegram", json={"init_data": data})
            ).status_code == 200
            second.cookies.set("veloexpress_session", cookie(43))
            assert (
                await second.post("/api/auth/telegram", json={"init_data": data})
            ).status_code == 401
    finally:
        await db.dispose()


# 3. Rider commands are limited and executed fairly.


def _today() -> date:
    return datetime.now(UTC).astimezone(ZoneInfo("Asia/Tbilisi")).date()


async def _rider_day(db: SharedDatabase) -> date:
    from tests.unit.test_payments_service import _fill, _setup

    lifts, _payments, _client, poll_id, day = await _setup(db)
    await _fill(lifts, poll_id, 0)
    return day


async def _command_count(db: SharedDatabase) -> int:
    async with db.session() as session:
        return int(await session.scalar(select(func.count()).select_from(AdminCommand)) or 0)


def _guest(day: date, lift_time: str = "8:30") -> dict[str, Any]:
    return {
        "command": {
            "request_id": str(uuid4()),
            "action": "guest",
            "service_date": day.isoformat(),
            "lift_time": lift_time,
            "delta": 1,
        }
    }


async def test_rider_commands_for_days_without_lifts_are_rejected_before_queueing() -> None:
    db = SharedDatabase()
    await db.create()
    try:
        day = await _rider_day(db)
        async with _client(_app(db, Members(100))) as client:
            client.cookies.set("veloexpress_session", cookie(100))
            headers = {"X-CSRF-Token": "csrf"}
            for body in (
                _guest(date(2030, 1, 5)),
                {
                    "command": {
                        "request_id": str(uuid4()),
                        "action": "claim_payment",
                        "service_date": "2030-01-05",
                    }
                },
            ):
                response = await client.post("/api/commands", json=body, headers=headers)
                assert response.status_code == 409
            assert await _command_count(db) == 0
            assert (
                await client.post("/api/commands", json=_guest(day), headers=headers)
            ).status_code == 202
    finally:
        await db.dispose()


async def test_rider_pending_commands_are_capped() -> None:
    db = SharedDatabase()
    await db.create()
    try:
        day = await _rider_day(db)
        async with _client(_app(db, Members(100))) as client:
            client.cookies.set("veloexpress_session", cookie(100))
            headers = {"X-CSRF-Token": "csrf"}
            first = _guest(day)
            assert (
                await client.post("/api/commands", json=first, headers=headers)
            ).status_code == 202
            for _ in range(MAX_PENDING_RIDER_COMMANDS - 1):
                response = await client.post("/api/commands", json=_guest(day), headers=headers)
                assert response.status_code == 202
            refused = await client.post("/api/commands", json=_guest(day), headers=headers)
            assert refused.status_code == 429
            assert "wait" in refused.json()["detail"].lower()
            assert await _command_count(db) == MAX_PENDING_RIDER_COMMANDS
            # A transport retry still returns the original request.
            retried = await client.post("/api/commands", json=first, headers=headers)
            assert retried.status_code == 202
    finally:
        await db.dispose()


async def test_command_submissions_are_rate_limited_per_user() -> None:
    db = SharedDatabase()
    await db.create()
    try:
        day = await _rider_day(db)
        clock = Clock()
        app = _app(db, Members(100, 101), command_rate=RateLimiter(limit=2, window=60, clock=clock))
        async with _client(app) as client:
            headers = {"X-CSRF-Token": "csrf"}
            client.cookies.set("veloexpress_session", cookie(100))
            for expected in (202, 202, 429):
                response = await client.post("/api/commands", json=_guest(day), headers=headers)
                assert response.status_code == expected
            client.cookies.set("veloexpress_session", cookie(101))
            assert (
                await client.post("/api/commands", json=_guest(day), headers=headers)
            ).status_code == 202
            client.cookies.set("veloexpress_session", cookie(100))
            clock.now += 61
            assert (
                await client.post("/api/commands", json=_guest(day), headers=headers)
            ).status_code == 202
    finally:
        await db.dispose()


async def test_queue_cap_is_enforced_at_insertion() -> None:
    db = SharedDatabase()
    await db.create()
    try:
        queue = CommandQueue(settings=settings(), session_factory=db.session)
        day = date(2026, 10, 3)
        for _ in range(MAX_PENDING_RIDER_COMMANDS):
            await queue.enqueue(
                CommandInput(request_id=uuid4(), action="claim_payment", service_date=day),
                actor_user_id=100,
            )
        with pytest.raises(CommandLimitError):
            await queue.enqueue(
                CommandInput(request_id=uuid4(), action="undo_payment", service_date=day),
                actor_user_id=100,
            )
        # Other riders and administrator operations are not affected.
        await queue.enqueue(
            CommandInput(request_id=uuid4(), action="claim_payment", service_date=day),
            actor_user_id=101,
        )
        await queue.enqueue(
            CommandInput(request_id=uuid4(), action="terms", price_gel=20, deadline_time="20:00"),
            actor_user_id=1,
        )
    finally:
        await db.dispose()


async def test_worker_takes_turns_between_actors() -> None:
    db = SharedDatabase()
    await db.create()
    try:
        queue = CommandQueue(settings=settings(), session_factory=db.session)
        day = date(2026, 10, 3)
        for _ in range(4):
            await queue.enqueue(
                CommandInput(request_id=uuid4(), action="claim_payment", service_date=day),
                actor_user_id=100,
            )
        await queue.enqueue(
            CommandInput(request_id=uuid4(), action="terms", price_gel=20, deadline_time="20:00"),
            actor_user_id=1,
        )
        await queue.enqueue(
            CommandInput(request_id=uuid4(), action="claim_payment", service_date=day),
            actor_user_id=101,
        )
        order: list[tuple[int, str]] = []

        class Recording:
            async def execute(self, spec: CommandInput, *, actor_user_id: int, **_: Any):
                order.append((actor_user_id, spec.action))
                return {"message": "ok"}

        while await queue.process_one(cast(Operations, Recording())):
            pass
        assert order[:3] == [(100, "claim_payment"), (1, "terms"), (101, "claim_payment")]
        assert len(order) == 6
    finally:
        await db.dispose()


# 4. An extra day from the cabinet matches the Telegram flow.


async def _operations(db: SharedDatabase):
    from tests.unit.test_poll_service import FakeTelegramClient

    from veloexpress_core.runtime import build_runtime

    config = settings()
    runtime = build_runtime(
        settings=config, session_factory=db.session, telegram_client=FakeTelegramClient()
    )
    return config, runtime, Operations(settings=config, runtime=runtime)


def _extra(day: date) -> CommandInput:
    return CommandInput(
        request_id=uuid4(),
        action="extra",
        service_date=day,
        first_lift_time="8:30",
        last_lift_time="13:30",
    )


async def test_extra_day_window_matches_the_telegram_date_choices() -> None:
    from veloexpress_bot.polls.extraday import date_choices

    db = SharedDatabase()
    await db.create()
    try:
        _config, _runtime, operations = await _operations(db)
        today = _today()
        choices = date_choices(today)
        for day in choices:
            await operations.preview(_extra(day))
        with pytest.raises(ValueError):
            await operations.preview(_extra(choices[-1] + timedelta(days=1)))
        with pytest.raises(ValueError):
            await operations.preview(_extra(today - timedelta(days=1)))
    finally:
        await db.dispose()


async def test_extra_day_posts_with_the_route_notice(monkeypatch) -> None:
    db = SharedDatabase()
    await db.create()
    try:
        _config, runtime, operations = await _operations(db)
        spec = _extra(_today() + timedelta(days=2))
        preview = await operations.preview(spec)
        create_poll = AsyncMock()
        monkeypatch.setattr(runtime.lifts, "create_poll", create_poll)
        await operations.execute(
            spec.model_copy(update={"expected_digest": preview["digest"]}),
            actor_user_id=1,
            identity={},
        )
        call = create_poll.await_args
        assert call is not None
        assert call.kwargs["include_notice"] is True
        assert call.kwargs["pin_after_send"] is False
    finally:
        await db.dispose()


async def test_duplicate_extra_day_fails_instead_of_asking_for_review(monkeypatch) -> None:
    from veloexpress_core.lifts import DuplicatePollError

    db = SharedDatabase()
    await db.create()
    try:
        config, runtime, operations = await _operations(db)
        spec = _extra(_today() + timedelta(days=2))
        preview = await operations.preview(spec)
        monkeypatch.setattr(
            runtime.lifts, "create_poll", AsyncMock(side_effect=DuplicatePollError("exists"))
        )
        queue = CommandQueue(settings=config, session_factory=db.session)
        command = await queue.enqueue(
            spec.model_copy(update={"expected_digest": preview["digest"]}), actor_user_id=1
        )
        assert await queue.process_one(operations)
        result = await queue.get(command["id"], actor_user_id=1)
        assert result is not None and result["status"] == "failed"
        assert "already has polls" in result["result"]["message"]
    finally:
        await db.dispose()


# 2. The rider card is personal and only opens in the rider's private chat.


@pytest.mark.parametrize("args", ["guests-20261003", "paid-20261003", "cash-20261003"])
async def test_deep_link_in_a_group_does_not_post_the_rider_card(args: str) -> None:
    from veloexpress_bot.bot.handlers import open_guest_form

    user = SimpleNamespace(id=100, username="alice", full_name="Alice")
    message = SimpleNamespace(
        chat=SimpleNamespace(type="supergroup", id=-100123),
        from_user=user,
        answer=AsyncMock(),
    )
    payments = Mock()
    payments.claim = AsyncMock()
    payments.open_rider_card = AsyncMock()
    await open_guest_form(cast(Message, message), cast(Any, SimpleNamespace(args=args)), payments)
    payments.claim.assert_not_awaited()
    payments.open_rider_card.assert_not_awaited()
    message.answer.assert_awaited_once()
    assert "private chat" in message.answer.await_args.args[0]


async def test_deep_link_in_the_private_chat_opens_the_card() -> None:
    from veloexpress_bot.bot.handlers import open_guest_form

    user = SimpleNamespace(id=100, username="alice", full_name="Alice")
    message = SimpleNamespace(
        chat=SimpleNamespace(type="private", id=100), from_user=user, answer=AsyncMock()
    )
    payments = Mock()
    payments.open_rider_card = AsyncMock()
    await open_guest_form(
        cast(Message, message), cast(Any, SimpleNamespace(args="guests-20261003")), payments
    )
    payments.open_rider_card.assert_awaited_once_with(
        telegram_user_id=100, private_chat_id=100, service_date=date(2026, 10, 3)
    )


@pytest.mark.parametrize(
    "data",
    [
        "guest:season",
        "guest:card",
        "guest:day:20261003",
        "guest:undo:20261003",
        "guest:pay:20261003",
    ],
)
@pytest.mark.parametrize("chat", ["group", "none"])
async def test_guest_callbacks_outside_the_private_chat_change_nothing(
    monkeypatch, data: str, chat: str
) -> None:
    from veloexpress_bot.bot import handlers

    message = SimpleNamespace(chat=SimpleNamespace(type="supergroup", id=-100123))
    monkeypatch.setattr(
        handlers, "_accessible_message", lambda _: message if chat == "group" else None
    )
    edit = AsyncMock()
    monkeypatch.setattr(handlers, "_edit_card", edit)
    callback = SimpleNamespace(data=data, from_user=SimpleNamespace(id=100), answer=AsyncMock())
    payments = Mock()
    await handlers.handle_guest_form(cast(CallbackQuery, callback), payments)
    assert payments.mock_calls == []
    edit.assert_not_awaited()
    callback.answer.assert_awaited_once()
    assert callback.answer.await_args.kwargs.get("show_alert") is True


async def test_guest_callback_in_the_private_chat_still_works(monkeypatch) -> None:
    from veloexpress_bot.bot import handlers

    message = SimpleNamespace(chat=SimpleNamespace(type="private", id=100))
    monkeypatch.setattr(handlers, "_accessible_message", lambda _: message)
    monkeypatch.setattr(handlers, "_edit_card", AsyncMock())
    callback = SimpleNamespace(
        data="guest:undo:20261003", from_user=SimpleNamespace(id=100), answer=AsyncMock()
    )
    payments = Mock()
    payments.undo = AsyncMock(return_value="Undone")
    payments.my_day_card = AsyncMock(return_value=SimpleNamespace(text="card", reply_markup=None))
    await handlers.handle_guest_form(cast(CallbackQuery, callback), payments)
    payments.undo.assert_awaited_once_with(service_date=date(2026, 10, 3), telegram_user_id=100)
    callback.answer.assert_awaited_once_with("Undone")
