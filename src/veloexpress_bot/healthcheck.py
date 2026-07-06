from __future__ import annotations

import argparse
import asyncio
import sys
from collections.abc import Sequence

from veloexpress_bot.config import Settings, get_settings
from veloexpress_bot.db.session import check_database, create_session_factory
from veloexpress_bot.health import HealthCheckError, check_heartbeat_file, heartbeat_config_from_env


async def run_healthcheck(
    *,
    settings: Settings,
    check_db: bool = True,
    check_heartbeat: bool = True,
) -> None:
    errors: list[str] = []

    if check_heartbeat:
        heartbeat_config = heartbeat_config_from_env()
        if heartbeat_config.path is not None:
            try:
                check_heartbeat_file(
                    heartbeat_config.path,
                    max_age_seconds=heartbeat_config.max_age_seconds,
                )
            except HealthCheckError as error:
                errors.append(str(error))

    if check_db:
        try:
            session_factory = create_session_factory(settings)
            await check_database(session_factory)
        except Exception as error:
            errors.append(f"database check failed: {type(error).__name__}")

    if errors:
        raise HealthCheckError("; ".join(errors))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Veloexpress bot container healthcheck.")
    parser.add_argument(
        "--skip-db",
        action="store_true",
        help="Do not check database connectivity.",
    )
    parser.add_argument(
        "--skip-heartbeat",
        action="store_true",
        help="Do not check the bot event-loop heartbeat file.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        asyncio.run(
            run_healthcheck(
                settings=get_settings(),
                check_db=not args.skip_db,
                check_heartbeat=not args.skip_heartbeat,
            )
        )
    except HealthCheckError as error:
        print(f"unhealthy: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    except Exception as error:
        print(f"unhealthy: unexpected {type(error).__name__}", file=sys.stderr)
        raise SystemExit(1) from error

    print("ok")


if __name__ == "__main__":
    main()
