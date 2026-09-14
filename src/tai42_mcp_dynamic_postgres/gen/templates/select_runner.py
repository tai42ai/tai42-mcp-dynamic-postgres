from typing import Any, Dict, List, Optional, Type, Union, cast

from psycopg import sql
from psycopg.rows import dict_row

from tai42_mcp_dynamic_postgres.database.connection import cursor
from tai42_mcp_dynamic_postgres.gen.filters.builder import ColumnResolver, build_where_clause
from tai42_mcp_dynamic_postgres.gen.filters.models import WhereFilter
from tai42_mcp_dynamic_postgres.gen.order.builder import build_order_by_clause
from tai42_mcp_dynamic_postgres.gen.order.models import OrderByItem


async def run_select(
    query: Union[sql.SQL, sql.Composed],
    resolver: ColumnResolver,
    where: Optional[WhereFilter],
    order_by: Optional[List[OrderByItem]],
    limit: Optional[int],
    offset: Optional[int],
    model: Optional[Type[Any]],
) -> List[Any]:
    """Append WHERE/ORDER BY/LIMIT/OFFSET to a base SELECT and execute it.

    Field names in ``where``/``order_by`` are validated against ``resolver``;
    values bind as parameters. Returns ``model`` instances if given, else dicts.
    A read issues no commit.
    """
    where_clause, params = build_where_clause(where, resolver)
    params = list(params)
    if where_clause is not None:
        query += sql.SQL(" WHERE ") + where_clause

    order_clause, order_params = build_order_by_clause(order_by, resolver)
    if order_clause is not None:
        query += sql.SQL(" ") + order_clause
        params.extend(order_params)

    if limit is not None:
        query += sql.SQL(" LIMIT %s")
        params.append(limit)

    if offset is not None:
        query += sql.SQL(" OFFSET %s")
        params.append(offset)

    async with cursor(row_factory=dict_row) as cur:
        await cur.execute(query, params)
        rows = cast(List[Dict[str, Any]], await cur.fetchall())

    if model is not None:
        return [model(**row) for row in rows]
    return [dict(row) for row in rows]
