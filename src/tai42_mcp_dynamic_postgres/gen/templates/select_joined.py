from typing import Any, Dict, List, Optional, Tuple, Type

from psycopg import sql

from tai42_mcp_dynamic_postgres.gen.filters.builder import resolver_from_column_map
from tai42_mcp_dynamic_postgres.gen.filters.models import WhereFilter
from tai42_mcp_dynamic_postgres.gen.order.models import OrderByItem
from tai42_mcp_dynamic_postgres.gen.templates.select_runner import run_select

# A select item as (identifier_parts, alias), e.g. (["public", "users", "id"], "public_users_id").
SelectItem = Tuple[List[str], str]
# A join predicate as (left_parts, right_parts).
JoinCondition = Tuple[List[str], List[str]]
# A join step as (join_table_parts, [conditions...]).
JoinStep = Tuple[List[str], List[JoinCondition]]


async def select_joined_tmpl(
    select_items: List[SelectItem],
    from_parts: List[str],
    joins: List[JoinStep],
    column_map: Dict[str, str],
    where: Optional[WhereFilter] = None,
    order_by: Optional[List[OrderByItem]] = None,
    limit: Optional[int] = None,
    offset: Optional[int] = None,
    model: Optional[Type[Any]] = None,
) -> List[Any]:
    # Every exposed column is projected explicitly (ignored columns are already
    # absent from ``select_items``); an empty projection would build an invalid
    # ``SELECT`` with no columns, so fail loudly instead.
    if not select_items:
        raise ValueError("Refusing to build a joined SELECT with no columns to project.")
    # The SELECT list, FROM table, and every JOIN identifier are emitted as
    # quoted ``sql.Identifier`` composables (never raw text), so reserved words
    # and mixed-case identifiers produce valid SQL.
    select_sql = sql.SQL("SELECT ") + sql.SQL(", ").join(
        sql.SQL("{} AS {}").format(sql.Identifier(*parts), sql.Identifier(alias)) for parts, alias in select_items
    )
    query: sql.Composed = select_sql + sql.SQL(" FROM {}").format(sql.Identifier(*from_parts))
    for table_parts, conditions in joins:
        on_sql = sql.SQL(" AND ").join(
            sql.SQL("{} = {}").format(sql.Identifier(*left), sql.Identifier(*right)) for left, right in conditions
        )
        query += sql.SQL(" LEFT JOIN {} ON ").format(sql.Identifier(*table_parts)) + on_sql

    return await run_select(query, resolver_from_column_map(column_map), where, order_by, limit, offset, model)
