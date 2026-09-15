"""Runtime helper backing the generated ``update`` tools."""

from typing import List, Optional, Sequence

from psycopg import sql
from psycopg.types.json import Json
from pydantic import BaseModel

from tai42_mcp_dynamic_postgres.database.connection import get_connection_pool
from tai42_mcp_dynamic_postgres.gen.filters.builder import build_where_clause, resolver_from_columns
from tai42_mcp_dynamic_postgres.gen.filters.models import WhereFilter


async def update_tmpl(
    table: str,
    columns: List[str],
    data: BaseModel,
    where: Optional[WhereFilter] = None,
    allow_unfiltered: bool = False,
    json_columns: Optional[Sequence[str]] = None,
) -> int:
    """Update rows of ``table`` with the fields set on ``data`` and return the row count.

    Only supplied fields are written; raises on an empty payload, and raises for
    a missing filter unless ``allow_unfiltered`` is set.
    """
    # Only columns the caller actually supplied are updated; an omitted field is
    # left untouched, while an explicit null is kept so it writes SET col = NULL.
    provided = data.model_fields_set
    update_fields = {k: v for k, v in data.model_dump().items() if k in provided}
    if not update_fields:
        raise ValueError(f"No fields provided to update on {table!r}; supply at least one field in `data`.")

    json_set = set(json_columns or [])

    resolver = resolver_from_columns(columns)
    where_clause, where_params = build_where_clause(where, resolver)

    if where_clause is None and not allow_unfiltered:
        raise ValueError(
            f"Refusing to update every row of {table!r} without a WHERE filter. Pass allow_unfiltered=True to override."
        )

    set_clauses = [sql.SQL("{} = %s").format(sql.Identifier(k)) for k in update_fields]
    set_values = [Json(v) if k in json_set else v for k, v in update_fields.items()]

    query = sql.SQL("UPDATE {table} SET ").format(table=sql.Identifier(*table.split(".")))
    query += sql.SQL(", ").join(set_clauses)

    if where_clause is not None:
        query += sql.SQL(" WHERE ") + where_clause

    # pool.connection() commits on success and rolls back on exception.
    pool = await get_connection_pool()
    async with pool.connection() as conn, conn.cursor() as cur:
        await cur.execute(query, set_values + where_params)
        return cur.rowcount
