import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, Optional

from psycopg import AsyncConnection, AsyncCursor
from psycopg.conninfo import make_conninfo
from psycopg.rows import AsyncRowFactory
from psycopg_pool import AsyncConnectionPool

from tai42_mcp_dynamic_postgres.config.settings import pg_settings
from tai42_mcp_dynamic_postgres.database.type_adapters import register_types_loaders

logger = logging.getLogger(__name__)

# The single process-wide pool. Built lazily on first use and closed once on
# shutdown; never rebuilt merely to be closed.
_pool: Optional[AsyncConnectionPool[AsyncConnection[Any]]] = None
_pool_lock = asyncio.Lock()


def _build_conninfo() -> str:
    """Assemble the libpq conninfo string from settings.

    ``make_conninfo`` escapes each value so passwords or names with special
    characters cannot break out. Built lazily and never stored at module scope, so
    the cleartext password is not a global.
    """
    return make_conninfo(
        host=pg_settings.host,
        port=pg_settings.port,
        dbname=pg_settings.db,
        user=pg_settings.user,
        password=pg_settings.password.get_secret_value(),
        # Cap any single statement so a runaway query cannot hold a pool slot.
        options=f"-c statement_timeout={pg_settings.statement_timeout}",
    )


async def get_connection_pool() -> AsyncConnectionPool[AsyncConnection[Any]]:
    global _pool
    async with _pool_lock:
        if _pool is None:
            pool: AsyncConnectionPool[AsyncConnection[Any]] = AsyncConnectionPool(
                conninfo=_build_conninfo(),
                min_size=pg_settings.pool_min_size,
                max_size=pg_settings.pool_max_size,
                timeout=pg_settings.pool_timeout,
                max_lifetime=pg_settings.pool_max_lifetime,
                # Register type loaders once per physical connection, not per checkout.
                configure=register_types_loaders,
                open=False,
            )
            try:
                await pool.open()
            except BaseException:
                # Opening spun up background workers; close the partial pool so
                # they are not orphaned, then propagate (``_pool`` stays None).
                await pool.close()
                raise
            _pool = pool
        return _pool


async def close_connection_pool() -> None:
    # Only close a pool that was actually created; do not build one just to close it.
    global _pool
    async with _pool_lock:
        if _pool is not None:
            await _pool.close()
            _pool = None


@asynccontextmanager
async def get_async_connection() -> AsyncGenerator[AsyncConnection[Any], None]:
    """Async context manager yielding a pooled DB connection in autocommit mode.

    The read/introspection path: autocommit leaves a SELECT-only connection IDLE,
    so returning it triggers no reset warning or rollback. Autocommit is reset to
    the pool default before return, or a later write checkout of the same physical
    connection would inherit it and lose its transactional guarantee.
    """
    pool: Optional[AsyncConnectionPool[AsyncConnection[Any]]] = None
    conn: Optional[AsyncConnection[Any]] = None

    try:
        pool = await get_connection_pool()
        conn = await pool.getconn()
        await conn.set_autocommit(True)
        yield conn
    except Exception:
        logger.exception("Error acquiring or using a pooled database connection")
        raise
    finally:
        if conn is not None and pool is not None:
            # Reset autocommit to the pool default so a later write checkout stays
            # transactional. A connection that died mid-statement raises here; log
            # but fall through to putconn (which discards a broken connection) and
            # never let this mask the original read error.
            try:
                await conn.set_autocommit(False)
            except Exception:
                logger.exception("Failed to reset autocommit before returning connection to the pool")
            await pool.putconn(conn)


@asynccontextmanager
async def cursor(
    name: str = "",
    *,
    binary: bool = False,
    row_factory: Optional[AsyncRowFactory[Any]] = None,
    scrollable: Optional[bool] = None,
    withhold: bool = False,
) -> AsyncGenerator[AsyncCursor[Any], None]:
    # Forward row_factory only when supplied so the connection's default
    # row factory is used otherwise.
    extra: dict[str, Any] = {} if row_factory is None else {"row_factory": row_factory}
    async with (
        get_async_connection() as conn,
        conn.cursor(
            name=name,
            binary=binary,
            scrollable=scrollable,
            withhold=withhold,
            **extra,
        ) as cur,
    ):
        yield cur
