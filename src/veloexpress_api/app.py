"""Same-origin HTTP adapter. Telegram work is executed by the bot command worker."""

import base64
import hashlib
import hmac
import secrets
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal
from urllib.parse import urlencode

import httpx
import jwt
from aiogram import Bot
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import text

from veloexpress_api.auth import AuthError, SessionSigner, validate_init_data
from veloexpress_api.settings import WebSettings
from veloexpress_bot.config import Settings
from veloexpress_bot.db.session import create_session_factory
from veloexpress_bot.payments.details import bank_details_text
from veloexpress_bot.telegram.client import AiogramTelegramClient
from veloexpress_core.analytics import DashboardAnalytics
from veloexpress_core.commands import (
    CONFIRMED_ACTIONS,
    USER_ACTIONS,
    CommandInput,
    CommandQueue,
    Operations,
)
from veloexpress_core.lifts import SessionFactory
from veloexpress_core.runtime import build_runtime

SESSION_COOKIE = "veloexpress_session"
OAUTH_COOKIE = "veloexpress_oauth"


class TelegramLogin(BaseModel):
    init_data: str = Field(max_length=16384)


class ConfirmedCommand(BaseModel):
    command: CommandInput
    confirmation: str | None = None


def create_app(
    *,
    settings: Settings | None = None,
    web_settings: WebSettings | None = None,
    session_factory: SessionFactory | None = None,
) -> FastAPI:
    config = settings or Settings()
    web = web_settings or WebSettings()
    signer = SessionSigner(web.session_secret)
    if config.app_env == "production" and not web.secure:
        raise ValueError("Production requires an HTTPS WEB_PUBLIC_URL")
    sessions = session_factory or create_session_factory(config)
    bot = Bot(token=config.telegram_bot_token)
    runtime = build_runtime(
        settings=config, session_factory=sessions, telegram_client=AiogramTelegramClient(bot)
    )
    analytics = DashboardAnalytics(settings=config, session_factory=sessions)
    queue = CommandQueue(settings=config, session_factory=sessions)
    operations = Operations(settings=config, runtime=runtime)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        async with sessions() as session:
            await session.execute(text("select 1"))
        try:
            yield
        finally:
            await bot.session.close()
            if session_factory is None:
                async_factory = sessions
                engine = getattr(async_factory, "kw", {}).get("bind")
                if engine is not None:
                    await engine.dispose()

    app = FastAPI(
        title="VeloExpress",
        version="1.0.0",
        lifespan=lifespan,
        docs_url=None if config.app_env == "production" else "/api/docs",
        openapi_url=None if config.app_env == "production" else "/api/openapi.json",
        redoc_url=None,
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        return response

    def check_origin(request: Request) -> None:
        origin = request.headers.get("origin")
        if origin and origin != web.public_url:
            raise HTTPException(403, "Untrusted origin")

    def identity(request: Request) -> dict[str, Any]:
        try:
            claims = signer.read(request.cookies.get(SESSION_COOKIE, ""), purpose="session")
            if type(claims.get("uid")) is not int or claims["uid"] <= 0:
                raise AuthError("Invalid identity")
            return claims
        except AuthError as error:
            raise HTTPException(401, "Sign in with Telegram") from error

    def admin(user: Annotated[dict[str, Any], Depends(identity)]) -> dict[str, Any]:
        if user["uid"] not in config.telegram_admin_ids:
            raise HTTPException(403, "Administrator access required")
        return user

    def csrf(request: Request, user: dict[str, Any]) -> None:
        check_origin(request)
        supplied = request.headers.get("x-csrf-token", "")
        if not supplied or not hmac.compare_digest(supplied, user["csrf"]):
            raise HTTPException(403, "Reload the page before trying again")

    def issue_session(
        response: Response, *, uid: int, name: str, username: str | None = None
    ) -> None:
        value = signer.sign(
            {
                "uid": uid,
                "name": name[:256],
                "username": username,
                "csrf": secrets.token_urlsafe(24),
            },
            purpose="session",
            ttl=web.session_seconds,
        )
        response.set_cookie(
            SESSION_COOKIE,
            value,
            max_age=web.session_seconds,
            httponly=True,
            secure=web.secure,
            samesite="lax",
            path="/api",
        )

    def today() -> date:
        from zoneinfo import ZoneInfo

        return datetime.now(UTC).astimezone(ZoneInfo(config.schedule_timezone)).date()

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        async with sessions() as session:
            await session.execute(text("select 1"))
        return {"status": "ok"}

    @app.get("/api/config")
    async def public_config() -> dict[str, Any]:
        return {
            "browser_login": bool(web.telegram_client_id and web.telegram_client_secret),
            "timezone": config.schedule_timezone,
        }

    @app.post("/api/auth/telegram")
    async def telegram_auth(body: TelegramLogin, request: Request, response: Response):
        check_origin(request)
        try:
            user = validate_init_data(body.init_data, config.telegram_bot_token)
        except AuthError as error:
            raise HTTPException(401, str(error)) from error
        issue_session(
            response,
            uid=user["id"],
            name=" ".join(filter(None, [user.get("first_name"), user.get("last_name")])),
            username=user.get("username"),
        )
        return {"ok": True}

    @app.get("/api/auth/login")
    async def browser_login():
        if not web.telegram_client_id or not web.telegram_client_secret:
            raise HTTPException(503, "Browser Telegram Login is not configured")
        state, verifier, nonce = (secrets.token_urlsafe(32) for _ in range(3))
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        params = {
            "client_id": web.telegram_client_id,
            "redirect_uri": f"{web.public_url}/api/auth/callback",
            "response_type": "code",
            "scope": "openid profile",
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        response = RedirectResponse(
            f"https://oauth.telegram.org/auth?{urlencode(params)}", status_code=302
        )
        response.set_cookie(
            OAUTH_COOKIE,
            signer.sign(
                {"state": state, "verifier": verifier, "nonce": nonce}, purpose="oauth", ttl=600
            ),
            max_age=600,
            httponly=True,
            secure=web.secure,
            samesite="lax",
            path="/api/auth",
        )
        return response

    @app.get("/api/auth/callback")
    async def browser_callback(request: Request, code: str = "", state: str = ""):
        try:
            flow = signer.read(request.cookies.get(OAUTH_COOKIE, ""), purpose="oauth")
            if not code or not state or not hmac.compare_digest(state, flow["state"]):
                raise AuthError("Invalid login state")
            async with httpx.AsyncClient(timeout=15) as client:
                token_response = await client.post(
                    "https://oauth.telegram.org/token",
                    auth=(web.telegram_client_id, web.telegram_client_secret),
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": f"{web.public_url}/api/auth/callback",
                        "code_verifier": flow["verifier"],
                    },
                )
                token_response.raise_for_status()
                token = token_response.json()["id_token"]
                jwks_response = await client.get("https://oauth.telegram.org/.well-known/jwks.json")
                jwks_response.raise_for_status()
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256":
                raise AuthError("Configure Telegram Login with RS256")
            key_data = next(k for k in jwks_response.json()["keys"] if k["kid"] == header["kid"])
            key = jwt.PyJWK.from_dict(key_data, algorithm="RS256").key
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=web.telegram_client_id,
                issuer="https://oauth.telegram.org",
                options={"require": ["exp", "iat", "sub", "nonce"]},
            )
            if not hmac.compare_digest(str(claims["nonce"]), flow["nonce"]):
                raise AuthError("Invalid login nonce")
            # Telegram 'id' is the numeric Bot API user id; 'sub' may be different.
            uid = claims.get("id")
            if type(uid) is not int or uid <= 0:
                raise AuthError("Telegram Login must include the profile scope")
        except (
            AuthError,
            jwt.PyJWTError,
            httpx.HTTPError,
            ValueError,
            KeyError,
            StopIteration,
        ) as error:
            raise HTTPException(401, "Telegram login failed; start again") from error
        response = RedirectResponse("/", status_code=303)
        issue_session(
            response,
            uid=uid,
            name=claims.get("name", str(uid)),
            username=claims.get("preferred_username"),
        )
        response.delete_cookie(OAUTH_COOKIE, path="/api/auth")
        return response

    @app.get("/api/session")
    async def session_view(user: Annotated[dict[str, Any], Depends(identity)]):
        return {
            "user_id": user["uid"],
            "name": user["name"],
            "admin": user["uid"] in config.telegram_admin_ids,
            "csrf": user["csrf"],
            "timezone": config.schedule_timezone,
            "today": today(),
        }

    @app.post("/api/auth/logout")
    async def logout(
        request: Request, response: Response, user: Annotated[dict[str, Any], Depends(identity)]
    ):
        csrf(request, user)
        response.delete_cookie(SESSION_COOKIE, path="/api")
        return {"ok": True}

    @app.get("/api/analytics")
    async def dashboard(
        user: Annotated[dict[str, Any], Depends(identity)],
        period: Literal["month", "year", "all", "custom"] = "month",
        start: date | None = None,
        end: date | None = None,
        personal: bool = False,
    ):
        own_id = user["uid"] if personal or user["uid"] not in config.telegram_admin_ids else None
        clock = today()
        if period == "custom":
            if start is None or end is None:
                raise HTTPException(422, "Select both dates")
        elif period == "month":
            start, end = clock.replace(day=1), clock - timedelta(days=1)
            if end < start:
                end = start - timedelta(days=1)
                start = end.replace(day=1)
        elif period == "year":
            start, end = date(clock.year, 1, 1), clock - timedelta(days=1)
            if end < start:
                start = date(clock.year - 1, 1, 1)
        else:
            start, end = (
                await analytics.first_record(user_id=own_id) or clock - timedelta(days=30),
                clock - timedelta(days=1),
            )
        assert start is not None and end is not None
        if start < date(2000, 1, 1) or end < start or start >= clock:
            raise HTTPException(422, "Select a historical date range from 2000 onwards")
        return await analytics.read(start=start, end=end, today=clock, user_id=own_id)

    @app.get("/api/admin/days")
    async def admin_days(_: Annotated[dict[str, Any], Depends(admin)]):
        result = []
        for day in await runtime.lifts.status_days():
            result.append(
                {
                    **asdict(day),
                    "running_count": day.running_count,
                    "confirmed_seat_count": day.confirmed_seat_count,
                    "lifts": [
                        {
                            **asdict(lift),
                            "seat_count": lift.seat_count,
                            "waiting_count": lift.waiting_count,
                            "running": lift.running,
                            "funded": lift.funded,
                        }
                        for lift in day.lifts
                    ],
                }
            )
        return result

    @app.get("/api/admin/days/{service_date}")
    async def admin_day(service_date: date, _: Annotated[dict[str, Any], Depends(admin)]):
        day = next(
            (d for d in await runtime.lifts.status_days() if d.service_date == service_date), None
        )
        if day is None:
            audit = await runtime.lifts.lift_day_audit(service_date=service_date)
            if audit is None:
                raise HTTPException(404, "Day not found")
            return {"historical": True, **asdict(audit)}
        details = []
        for lift in day.lifts:
            detail = await runtime.lifts.lift_detail(service_date=service_date, lift_time=lift.time)
            details.append(
                {
                    **asdict(lift),
                    "seat_count": lift.seat_count,
                    "waiting_count": lift.waiting_count,
                    "running": lift.running,
                    "funded": lift.funded,
                    "riders": [asdict(r) for r in detail[1]] if detail else [],
                }
            )
        bookings = await runtime.payments.day_bookings(service_date)
        riders = []
        if bookings:
            views = runtime.payments.rider_views(bookings)
            riders = [
                {"user_id": uid, "label": label, **asdict(views[uid])}
                for uid, (_username, label) in bookings.labels_by_user.items()
                if uid in views
            ]
        return {
            "historical": False,
            **asdict(day),
            "lifts": details,
            "riders": riders,
            "withdrawals": [
                asdict(entry)
                for entry in await runtime.lifts.booking_withdrawals(service_date=service_date)
            ],
            "commands": await queue.recent(service_date=service_date),
        }

    @app.get("/api/admin/planning")
    async def planning(_: Annotated[dict[str, Any], Depends(admin)]):
        return await operations.planning_view()

    @app.get("/api/admin/days/{service_date}/lifts/{lift_time}/order")
    async def booking_order(
        service_date: date,
        lift_time: str,
        _: Annotated[dict[str, Any], Depends(admin)],
    ):
        try:
            return await runtime.lifts.booking_order_view(
                service_date=service_date, lift_time=lift_time
            )
        except (ValueError, KeyError) as error:
            raise HTTPException(409, str(error)) from error

    @app.get("/api/admin/refunds")
    async def refunds(_: Annotated[dict[str, Any], Depends(admin)]):
        return [asdict(report) for report in await runtime.payments.recent_refund_reports(limit=50)]

    @app.get("/api/admin/audit")
    async def audit(_: Annotated[dict[str, Any], Depends(admin)]):
        return await queue.recent()

    @app.get("/api/my-days")
    async def my_days(user: Annotated[dict[str, Any], Depends(identity)]):
        result = []
        days = await runtime.lifts.status_days()
        views = await runtime.payments.rider_days(
            service_dates=(day.service_date for day in days), telegram_user_id=user["uid"]
        )
        for day in days:
            view = views.get(day.service_date)
            result.append(
                {
                    "service_date": day.service_date,
                    "past": day.past,
                    "lifts": [
                        {
                            "time": lift.time,
                            "seats": lift.seat_count,
                            "capacity": lift.capacity,
                            "waiting": lift.waiting_count,
                            "cancelled": lift.cancelled,
                            "running": lift.running,
                        }
                        for lift in day.lifts
                    ],
                    "booking": asdict(view) if view else None,
                }
            )
        from veloexpress_bot.deeplinks import topic_link

        return {
            "days": result,
            "polls_url": topic_link(
                chat_id=config.telegram_target_chat_id or 0,
                thread_id=config.telegram_target_thread_id,
            ),
            "bank_details": bank_details_text(),
        }

    @app.post("/api/admin/preview")
    async def preview(
        spec: CommandInput, request: Request, user: Annotated[dict[str, Any], Depends(admin)]
    ):
        csrf(request, user)
        try:
            result = await operations.preview(spec)
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        confirmation = signer.sign(
            {
                "uid": user["uid"],
                "action": spec.action,
                "spec": spec.model_dump(mode="json", exclude={"request_id", "expected_digest"}),
                "digest": result["digest"],
            },
            purpose="confirmation",
            ttl=300,
        )
        return {**result, "confirmation": confirmation}

    @app.post("/api/commands", status_code=202)
    async def submit(
        body: ConfirmedCommand, request: Request, user: Annotated[dict[str, Any], Depends(identity)]
    ):
        csrf(request, user)
        spec = body.command
        if spec.action not in USER_ACTIONS and user["uid"] not in config.telegram_admin_ids:
            raise HTTPException(403, "Administrator access required")
        # A transport retry retrieves the original command even after its
        # confirmation expires; it never schedules a second execution.
        try:
            existing = await queue.existing(spec, actor_user_id=user["uid"])
        except ValueError as error:
            raise HTTPException(409, str(error)) from error
        if existing is not None:
            return existing
        if spec.action in CONFIRMED_ACTIONS:
            try:
                confirmation = signer.read(body.confirmation or "", purpose="confirmation")
                values = spec.model_dump(mode="json", exclude={"request_id", "expected_digest"})
                if confirmation["uid"] != user["uid"] or confirmation["spec"] != values:
                    raise AuthError("Confirmation does not match this operation")
                spec = spec.model_copy(update={"expected_digest": confirmation["digest"]})
            except AuthError as error:
                raise HTTPException(409, "Open the confirmation again") from error
        try:
            return await queue.enqueue(
                spec,
                actor_user_id=user["uid"],
                identity={"name": user["name"], "username": user.get("username")},
            )
        except ValueError as error:
            raise HTTPException(409, str(error)) from error

    @app.get("/api/commands/{command_id}")
    async def command_status(command_id: int, user: Annotated[dict[str, Any], Depends(identity)]):
        result = await queue.get(command_id, actor_user_id=user["uid"])
        if result is None:
            raise HTTPException(404, "Command not found")
        return result

    return app
