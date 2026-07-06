from __future__ import annotations

import json
import logging
import os
from asyncio import Task, create_task, sleep
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import time

logger = logging.getLogger(__name__)

HEARTBEAT_FILE_ENV = "APP_HEALTH_HEARTBEAT_FILE"
HEARTBEAT_INTERVAL_SECONDS_ENV = "APP_HEALTH_HEARTBEAT_INTERVAL_SECONDS"
HEARTBEAT_MAX_AGE_SECONDS_ENV = "APP_HEALTH_MAX_AGE_SECONDS"

DEFAULT_HEARTBEAT_INTERVAL_SECONDS = 15.0
DEFAULT_HEARTBEAT_MAX_AGE_SECONDS = 90.0


class HealthCheckError(RuntimeError):
    pass


@dataclass(frozen=True)
class HeartbeatConfig:
    path: Path | None
    interval_seconds: float = DEFAULT_HEARTBEAT_INTERVAL_SECONDS
    max_age_seconds: float = DEFAULT_HEARTBEAT_MAX_AGE_SECONDS


@dataclass(frozen=True)
class HeartbeatStatus:
    path: Path
    age_seconds: float
    max_age_seconds: float


def heartbeat_config_from_env(environ: Mapping[str, str] | None = None) -> HeartbeatConfig:
    env = os.environ if environ is None else environ
    raw_path = env.get(HEARTBEAT_FILE_ENV, "").strip()
    return HeartbeatConfig(
        path=Path(raw_path) if raw_path else None,
        interval_seconds=_positive_float_from_env(
            env,
            HEARTBEAT_INTERVAL_SECONDS_ENV,
            DEFAULT_HEARTBEAT_INTERVAL_SECONDS,
        ),
        max_age_seconds=_positive_float_from_env(
            env,
            HEARTBEAT_MAX_AGE_SECONDS_ENV,
            DEFAULT_HEARTBEAT_MAX_AGE_SECONDS,
        ),
    )


def write_heartbeat(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "ok",
        "updated_at": datetime.now(UTC).isoformat(),
        "pid": os.getpid(),
    }
    temporary_path = path.with_name(f".{path.name}.tmp")
    temporary_path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    temporary_path.replace(path)


def check_heartbeat_file(
    path: Path,
    *,
    max_age_seconds: float,
    now: float | None = None,
) -> HeartbeatStatus:
    try:
        modified_at = path.stat().st_mtime
    except FileNotFoundError as error:
        raise HealthCheckError(f"heartbeat file is missing: {path}") from error

    age_seconds = max(0.0, (time() if now is None else now) - modified_at)
    if age_seconds > max_age_seconds:
        raise HealthCheckError(
            f"heartbeat file is stale: age={age_seconds:.1f}s max={max_age_seconds:.1f}s"
        )
    return HeartbeatStatus(
        path=path,
        age_seconds=age_seconds,
        max_age_seconds=max_age_seconds,
    )


async def heartbeat_loop(path: Path, *, interval_seconds: float) -> None:
    while True:
        try:
            write_heartbeat(path)
        except OSError:
            logger.exception("Failed to write health heartbeat", extra={"path": str(path)})
        await sleep(interval_seconds)


def start_heartbeat_task(config: HeartbeatConfig | None = None) -> Task[None] | None:
    resolved_config = heartbeat_config_from_env() if config is None else config
    if resolved_config.path is None:
        return None

    try:
        write_heartbeat(resolved_config.path)
    except OSError:
        logger.exception(
            "Failed to write initial health heartbeat",
            extra={"path": str(resolved_config.path)},
        )

    return create_task(
        heartbeat_loop(
            resolved_config.path,
            interval_seconds=resolved_config.interval_seconds,
        ),
        name="veloexpress-health-heartbeat",
    )


def _positive_float_from_env(
    environ: Mapping[str, str],
    name: str,
    default: float,
) -> float:
    raw_value = environ.get(name, "").strip()
    if not raw_value:
        return default
    try:
        value = float(raw_value)
    except ValueError:
        return default
    return value if value > 0 else default
