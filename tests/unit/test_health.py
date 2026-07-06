import os
from pathlib import Path
from time import time
from typing import Any, cast

import pytest

from veloexpress_bot.config import Settings
from veloexpress_bot.health import (
    HEARTBEAT_FILE_ENV,
    HEARTBEAT_INTERVAL_SECONDS_ENV,
    HEARTBEAT_MAX_AGE_SECONDS_ENV,
    HealthCheckError,
    check_heartbeat_file,
    heartbeat_config_from_env,
    write_heartbeat,
)
from veloexpress_bot.healthcheck import run_healthcheck


def make_settings(**kwargs: object) -> Settings:
    settings_factory = cast(Any, Settings)
    return settings_factory(_env_file=None, **kwargs)


def test_heartbeat_config_reads_env() -> None:
    config = heartbeat_config_from_env(
        {
            HEARTBEAT_FILE_ENV: "/tmp/veloexpress-health.json",
            HEARTBEAT_INTERVAL_SECONDS_ENV: "5",
            HEARTBEAT_MAX_AGE_SECONDS_ENV: "30",
        }
    )

    assert config.path == Path("/tmp/veloexpress-health.json")
    assert config.interval_seconds == 5
    assert config.max_age_seconds == 30


def test_check_heartbeat_file_accepts_fresh_file(tmp_path: Path) -> None:
    heartbeat_path = tmp_path / "heartbeat.json"
    write_heartbeat(heartbeat_path)

    status = check_heartbeat_file(heartbeat_path, max_age_seconds=90)

    assert status.path == heartbeat_path
    assert status.age_seconds < 90


def test_check_heartbeat_file_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(HealthCheckError, match="heartbeat file is missing"):
        check_heartbeat_file(tmp_path / "missing.json", max_age_seconds=90)


def test_check_heartbeat_file_rejects_stale_file(tmp_path: Path) -> None:
    heartbeat_path = tmp_path / "heartbeat.json"
    write_heartbeat(heartbeat_path)
    stale_timestamp = time() - 120
    os.utime(heartbeat_path, (stale_timestamp, stale_timestamp))

    with pytest.raises(HealthCheckError, match="heartbeat file is stale"):
        check_heartbeat_file(heartbeat_path, max_age_seconds=90)


@pytest.mark.asyncio
async def test_run_healthcheck_checks_database_and_heartbeat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    heartbeat_path = tmp_path / "heartbeat.json"
    write_heartbeat(heartbeat_path)
    monkeypatch.setenv(HEARTBEAT_FILE_ENV, str(heartbeat_path))

    await run_healthcheck(
        settings=make_settings(database_url="sqlite+aiosqlite:///:memory:"),
        check_db=True,
        check_heartbeat=True,
    )
