"""Click entry point that generates the tools and runs the MCP server."""

import asyncio
import logging
import sys
from typing import Literal, Optional, Tuple

import click

from tai42_mcp_dynamic_postgres.core.app import mcp_app
from tai42_mcp_dynamic_postgres.database.connection import close_connection_pool, get_async_connection
from tai42_mcp_dynamic_postgres.gen.loader import load_dynamic_tools

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

TransportName = Literal["stdio", "http", "sse", "streamable-http"]


async def runner(
    overwrite: bool,
    readonly: bool,
    allow_unfiltered: bool,
    select_joined: Tuple[str, ...],
    ignore_insert_column: Tuple[str, ...],
    ignore_select_column: Tuple[str, ...],
    ignore_update_column: Tuple[str, ...],
    ignore_select_joined_column: Tuple[str, ...],
    transport: TransportName,
    host: str,
    port: int,
) -> Optional[int]:
    """Generate the tools, verify the database, and run the server on ``transport``.

    Returns a process exit code on interruption or failure, or ``None`` on a
    clean shutdown.
    """
    if transport == "stdio" and (host != "127.0.0.1" or port != 8000):
        raise click.BadParameter("Host and port should not be set when using 'stdio' transport.")

    await load_dynamic_tools(
        overwrite=overwrite,
        readonly=readonly,
        allow_unfiltered=allow_unfiltered,
        ignore_insert_columns=list(ignore_insert_column),
        ignore_select_columns=list(ignore_select_column),
        ignore_update_columns=list(ignore_update_column),
        ignore_select_joined_columns=list(ignore_select_joined_column),
        select_joined=[group.split(",") for group in select_joined or []],
    )

    # ping pg (fail fast on a bad connection before starting the server)
    async with get_async_connection():
        pass

    try:
        if transport == "stdio":
            await mcp_app.run_async(transport=transport)
        else:
            await mcp_app.run_async(transport=transport, host=host, port=port)
    except KeyboardInterrupt:
        logging.info("KeyboardInterrupt")
        return 130
    except Exception:
        logging.exception("Server terminated with an unexpected error")
        return 1
    finally:
        # Safety net for paths that never reached the server; the app lifespan
        # already closes the pool on clean shutdown. No-op when no pool is open.
        try:
            await close_connection_pool()
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("Error during connection pool closure")
    return None


@click.command()
@click.option(
    "--overwrite/--no-overwrite",
    default=True,
    show_default=True,
    help="Regenerate the tool files on startup so they reflect the current schema. "
    "Use --no-overwrite to reuse existing generated files instead.",
)
@click.option(
    "--readonly",
    is_flag=True,
    help="Generate only read-only tools (e.g., select functions). No insert or update tools will be generated.",
)
@click.option(
    "--allow-unfiltered",
    is_flag=True,
    help="Allow update/delete tools to run without a WHERE filter (affects every row). "
    "Off by default: unfiltered updates and deletes raise an error.",
)
@click.option(
    "--select-joined",
    multiple=True,
    help="Join groups for select, e.g., --select-joined table1,table2,table3",
)
@click.option(
    "--ignore-insert-column",
    multiple=True,
    default=["id"],
    show_default=True,
    help="Columns to ignore when generating insert functions.",
)
@click.option(
    "--ignore-select-column",
    multiple=True,
    default=[],
    help="Columns to ignore when generating select functions.",
)
@click.option(
    "--ignore-update-column",
    multiple=True,
    default=["id"],
    show_default=True,
    help="Columns to ignore when generating update functions.",
)
@click.option(
    "--ignore-select-joined-column",
    multiple=True,
    default=[],
    help="Columns to ignore when generating select_joined functions.",
)
@click.option(
    "-t",
    "--transport",
    type=click.Choice(["stdio", "http", "sse", "streamable-http"]),
    default="stdio",
    show_default=True,
    help="Transport mechanism for the MCP server.",
)
@click.option(
    "--host",
    default="127.0.0.1",
    show_default=True,
    help="Host to bind the server to (used only with HTTP/SSE transports).",
)
@click.option(
    "--port",
    default=8000,
    show_default=True,
    type=int,
    help="Port number to bind the server to (used only with HTTP/SSE transports).",
)
def main(
    overwrite: bool,
    readonly: bool,
    allow_unfiltered: bool,
    select_joined: Tuple[str, ...],
    ignore_insert_column: Tuple[str, ...],
    ignore_select_column: Tuple[str, ...],
    ignore_update_column: Tuple[str, ...],
    ignore_select_joined_column: Tuple[str, ...],
    transport: TransportName,
    host: str,
    port: int,
) -> None:
    """Generate scoped PostgreSQL CRUD MCP tools from a live schema."""
    sys.exit(
        asyncio.run(
            runner(
                overwrite,
                readonly,
                allow_unfiltered,
                select_joined,
                ignore_insert_column,
                ignore_select_column,
                ignore_update_column,
                ignore_select_joined_column,
                transport,
                host,
                port,
            )
        )
    )


if __name__ == "__main__":
    main()
