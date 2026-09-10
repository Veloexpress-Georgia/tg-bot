"""Transaction-scoped serialization for operations whose row may not exist yet."""

import hashlib

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def transaction_lock(session: AsyncSession, scope: str) -> None:
    # PostgreSQL releases this on commit/rollback, including connection loss.
    # SQLite is used only by serial unit fixtures; concurrency tests use Postgres.
    if session.get_bind().dialect.name != "postgresql":
        return
    key = int.from_bytes(hashlib.sha256(scope.encode()).digest()[:8], "big", signed=True)
    await session.execute(select(func.pg_advisory_xact_lock(key)))
