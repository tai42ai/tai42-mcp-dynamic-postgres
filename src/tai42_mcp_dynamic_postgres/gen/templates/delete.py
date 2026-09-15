"""Runtime helper backing the generated ``delete`` tools."""

from typing import List, Optional

from psycopg import sql

from tai42_mcp_dynamic_postgres.database.connection import get_connection_pool
from tai42_mcp_dynamic_postgres.gen.filters.builder import build_where_clause, resolver_from_columns
from tai42_mcp_dynamic_postgres.gen.filters.models import WhereFilter

_DELETE_SQL_TEMPLATE = "DELETE FROM {table}"


async def delete_tmpl(
    table: str,
    columns: List[str],
    where: Optional[WhereFilter] = None,
    allow_unfiltered: bool = False,
) -> int:
    """Delete rows from ``table`` matching ``where`` and return the row count.

    Raises if no filter is given unless ``allow_unfiltered`` is set.
    """
    resolver = resolver_from_columns(columns)
    where_clause, params = build_where_clause(where, resolver)

    if where_clause is None and not allow_unfiltered:
        raise ValueError(
            f"Refusing to delete every row of {table!r} without a WHERE filter. Pass allow_unfiltered=True to override."
        )

    query = sql.SQL(_DELETE_SQL_TEMPLATE).format(table=sql.Identifier(*table.split(".")))

    if where_clause is not None:
        query += sql.SQL(" WHERE ") + where_clause

    # pool.connection() commits on success and rolls back on exception.
    pool = await get_connection_pool()
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(query, params)
        return cur.rowcount
