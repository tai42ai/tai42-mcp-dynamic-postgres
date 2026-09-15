"""Build parameterized SQL WHERE clauses from :class:`WhereFilter` payloads."""

from typing import Any, Dict, List, Optional, Tuple

from psycopg import sql

from tai42_mcp_dynamic_postgres.gen.filters.models import FilterOp, LogicalFilter, WhereFilter
from tai42_mcp_dynamic_postgres.gen.vector import KNN_OPS, vector_literal

# Comparison operators that map directly to `<column> <op> %s`. Values are
# pre-built from string literals so they are never assembled from runtime text.
_COMPARISON_OPS: Dict[str, sql.SQL] = {
    "eq": sql.SQL("="),
    "ne": sql.SQL("!="),
    "gt": sql.SQL(">"),
    "gte": sql.SQL(">="),
    "lt": sql.SQL("<"),
    "lte": sql.SQL("<="),
    "like": sql.SQL("LIKE"),
    "not_like": sql.SQL("NOT LIKE"),
    "ilike": sql.SQL("ILIKE"),
    "not_ilike": sql.SQL("NOT ILIKE"),
}

# A resolver maps an agent-facing field name to the SQL identifier it is allowed
# to reference. A field absent from the resolver is rejected, so column names
# never reach the query as raw, unescaped text.
ColumnResolver = Dict[str, sql.Identifier]


def resolver_from_columns(columns: List[str]) -> ColumnResolver:
    """Build a resolver for a single table from its unqualified column names."""
    return {col: sql.Identifier(col) for col in columns}


def resolver_from_column_map(column_map: Optional[Dict[str, str]]) -> ColumnResolver:
    """Build a resolver for a join from an alias -> qualified-name map.

    Qualified names are dotted (``schema.table.column``) and become multi-part
    identifiers so each part is quoted independently.
    """
    if not column_map:
        return {}
    return {alias: sql.Identifier(*qualified.split(".")) for alias, qualified in column_map.items()}


def _resolve(field: str, resolver: ColumnResolver) -> sql.Identifier:
    if field not in resolver:
        raise ValueError(f"Unknown filter field {field!r}. Allowed fields: {sorted(resolver)}")
    return resolver[field]


def _render_condition(ident: sql.Identifier, condition: FilterOp, params: List[Any]) -> List[sql.Composable]:
    clauses: List[sql.Composable] = []
    for operator, value in condition.model_dump(exclude_none=True, by_alias=True).items():
        if operator == "knn":
            knn = condition.knn
            if knn is None:
                raise ValueError("knn filter present without operands")
            clauses.append(sql.SQL("{} {} (%s)::vector < %s").format(ident, KNN_OPS[knn.distance]))
            params.extend([vector_literal(knn.query), knn.threshold])
        elif operator in _COMPARISON_OPS:
            clauses.append(sql.SQL("{} {} %s").format(ident, _COMPARISON_OPS[operator]))
            params.append(value)
        elif operator == "is_null":
            null_sql = sql.SQL("NULL") if value else sql.SQL("NOT NULL")
            clauses.append(sql.SQL("{} IS {}").format(ident, null_sql))
        elif operator == "in":
            placeholders = sql.SQL(", ").join(sql.Placeholder() for _ in value)
            clauses.append(sql.SQL("{} IN ({})").format(ident, placeholders))
            params.extend(value)
        elif operator == "not_in":
            placeholders = sql.SQL(", ").join(sql.Placeholder() for _ in value)
            clauses.append(sql.SQL("{} NOT IN ({})").format(ident, placeholders))
            params.extend(value)
        elif operator == "between":
            clauses.append(sql.SQL("{} BETWEEN %s AND %s").format(ident))
            params.extend(value)
        else:
            raise ValueError(f"Unsupported filter operator {operator!r}")
    return clauses


def _build_logical(logical: LogicalFilter, resolver: ColumnResolver) -> Tuple[Optional[sql.Composable], List[Any]]:
    """Render an AND/OR/NOT node, recursing into each subfilter via ``_build``.

    Each present combinator becomes a parenthesized group; the groups are ANDed
    together. A combinator whose subfilters all resolve empty contributes nothing.
    """
    params: List[Any] = []
    groups: List[sql.Composable] = []

    def add_group(joiner: sql.SQL, subfilters: List[WhereFilter]) -> None:
        parts: List[sql.Composable] = []
        for sub in subfilters:
            clause, sub_params = _build(sub, resolver)
            params.extend(sub_params)
            if clause is not None:
                parts.append(clause)
        if parts:
            groups.append(sql.SQL("({})").format(joiner.join(parts)))

    if logical.AND:
        add_group(sql.SQL(" AND "), logical.AND)
    if logical.OR:
        add_group(sql.SQL(" OR "), logical.OR)
    if logical.NOT:
        clause, sub_params = _build(logical.NOT, resolver)
        params.extend(sub_params)
        if clause is not None:
            groups.append(sql.SQL("(NOT ({}))").format(clause))

    if not groups:
        return None, params
    return sql.SQL(" AND ").join(groups), params


def _build_field_conditions(
    field_map: Dict[str, FilterOp], resolver: ColumnResolver
) -> Tuple[Optional[sql.Composable], List[Any]]:
    """Render a field -> condition map into ANDed comparison clauses."""
    params: List[Any] = []
    clauses: List[sql.Composable] = []
    for field, condition in field_map.items():
        ident = _resolve(field, resolver)
        clauses.extend(_render_condition(ident, condition, params))
    if not clauses:
        return None, params
    return sql.SQL(" AND ").join(clauses), params


def _build(op: Optional[WhereFilter], resolver: ColumnResolver) -> Tuple[Optional[sql.Composable], List[Any]]:
    if not op:
        return None, []

    inner = op.root
    if isinstance(inner, LogicalFilter):
        return _build_logical(inner, resolver)
    # inner is Dict[str, FilterOp] (the only other member of the root union).
    return _build_field_conditions(inner, resolver)


def build_where_clause(
    op: Optional[WhereFilter], resolver: ColumnResolver
) -> Tuple[Optional[sql.Composable], List[Any]]:
    """Build a parameterized WHERE clause from a validated filter tree.

    Column names are emitted only as quoted SQL identifiers drawn from
    ``resolver``; unknown fields raise. Values are always bound as parameters.

    ``op is None`` (no ``where`` supplied) returns ``(None, [])``. A ``where``
    that *was* supplied but resolves to no predicate (e.g. ``{}`` or
    ``{"AND": []}``) raises, rather than silently dropping the WHERE clause and
    matching every row.
    """
    clause, params = _build(op, resolver)
    if op is not None and clause is None:
        raise ValueError(
            "The provided `where` filter is empty: it resolves to no predicate. "
            "Omit `where` entirely to match all rows, or supply a real condition."
        )
    return clause, params
