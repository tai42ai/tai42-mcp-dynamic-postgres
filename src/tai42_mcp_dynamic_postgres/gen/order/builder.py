"""Build parameterized SQL ORDER BY clauses from :class:`OrderByItem` lists."""

from typing import Any, Dict, List, Optional, Tuple

from psycopg import sql

from tai42_mcp_dynamic_postgres.gen.filters.builder import ColumnResolver
from tai42_mcp_dynamic_postgres.gen.order.models import OrderByItem
from tai42_mcp_dynamic_postgres.gen.vector import KNN_OPS, vector_literal

# Sort directions, pre-built from literals (the model already constrains the
# value to ASC/DESC).
_DIRECTIONS: Dict[str, sql.SQL] = {"ASC": sql.SQL("ASC"), "DESC": sql.SQL("DESC")}
# NULLS placement, pre-built from literals (the model constrains to FIRST/LAST).
_NULLS: Dict[str, sql.SQL] = {"FIRST": sql.SQL("NULLS FIRST"), "LAST": sql.SQL("NULLS LAST")}


def build_order_by_clause(
    order_by: Optional[List[OrderByItem]],
    resolver: ColumnResolver,
) -> Tuple[Optional[sql.Composable], List[Any]]:
    """Build a parameterized ORDER BY clause from validated order items.

    Column names are emitted only as quoted identifiers from ``resolver`` (unknown
    fields raise); ``direction``/``nulls`` are constrained literals. Returns
    ``(None, [])`` when there is nothing to order by.
    """
    if not order_by:
        return None, []

    parts: List[sql.Composable] = []
    order_params: List[Any] = []

    for item in order_by:
        if item.field not in resolver:
            raise ValueError(f"Unknown order_by field {item.field!r}. Allowed fields: {sorted(resolver)}")
        ident = resolver[item.field]
        part: sql.Composable
        if item.knn:
            knn = item.knn
            part = sql.SQL("{} {} (%s)::vector {}").format(ident, KNN_OPS[knn.distance], _DIRECTIONS[knn.direction])
            order_params.append(vector_literal(knn.query))
        else:
            part = sql.SQL("{} {}").format(ident, _DIRECTIONS[item.direction])
        if item.nulls:
            part = part + sql.SQL(" ") + _NULLS[item.nulls]
        parts.append(part)

    return sql.SQL("ORDER BY ") + sql.SQL(", ").join(parts), order_params
