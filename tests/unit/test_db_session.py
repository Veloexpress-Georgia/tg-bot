from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from veloexpress_bot.db.session import check_database


async def test_check_database_runs_select_one() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        await check_database(factory)
    finally:
        await engine.dispose()
