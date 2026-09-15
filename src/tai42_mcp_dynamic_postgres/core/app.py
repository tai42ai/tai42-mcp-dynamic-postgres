"""The shared FastMCP application and the lifespan that owns the connection pool."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastmcp import FastMCP

from tai42_mcp_dynamic_postgres.database.connection import close_connection_pool, get_connection_pool


@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncGenerator[None, None]:
    """Open the connection pool on startup and close it on shutdown.

    Bound to the app lifespan, so the pool closes whenever the server stops,
    whatever entry point (CLI, embedding host, tests) started it.
    """
    await get_connection_pool()
    try:
        yield
    finally:
        await close_connection_pool()


mcp_app: FastMCP = FastMCP(lifespan=lifespan)
