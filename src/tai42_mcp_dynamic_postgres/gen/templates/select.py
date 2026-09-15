"""Runtime helper backing the generated single-table ``select`` tools."""

from typing import Any, List, Optional, Sequence, Type

from psycopg import sql

from tai42_mcp_dynamic_postgres.gen.filters.builder import resolver_from_columns
from tai42_mcp_dynamic_postgres.gen.filters.models import WhereFilter
from tai42_mcp_dynamic_postgres.gen.order.models import OrderByItem
from tai42_mcp_dynamic_postgres.gen.templates.select_runner import run_select


async def select_tmpl(
    table: str,
    columns: List[str],
    where: Optional[WhereFilter] = None,
    order_by: Optional[List[OrderByItem]] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    model: Optional[Type[Any]] = None,
    select_columns: Optional[Sequence[str]] = None,
) -> List[Any]:
    """Select rows, projecting exactly the exposed columns.

    ``select_columns`` is the SQL projection, so hidden columns are never fetched;
    ``columns`` is the full allowlist ``where``/``order_by`` may reference, so a
    hidden column can still be filtered without being returned. Omitting
    ``select_columns`` projects the whole allowlist; an empty projection raises.
    """
    projection = list(select_columns) if select_columns is not None else columns
    if not projection:
        raise ValueError(f"Refusing to build a SELECT on {table!r} with no columns to project.")
    projection_sql = sql.SQL(", ").join(sql.Identifier(col) for col in projection)
    query = sql.SQL("SELECT {} FROM {}").format(projection_sql, sql.Identifier(*table.split(".")))
    return await run_select(query, resolver_from_columns(columns), where, order_by, limit, offset, model)
