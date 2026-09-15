"""Generator for ``select_joined`` tools over FK-related table groups."""

from typing import Dict, Iterable, List, Optional, Tuple

from tai42_mcp_dynamic_postgres.gen.builders.base_gen import BaseGen, Chunk
from tai42_mcp_dynamic_postgres.gen.schema.codegen import sql_columns_to_pydantic_model
from tai42_mcp_dynamic_postgres.gen.schema.introspect import ForeignKey, TableInfo

_FUNC_PREFIX = "select_joined"

# A single join predicate as (left_identifier_parts, right_identifier_parts),
# e.g. (["public", "users", "org_id"], ["public", "orgs", "id"]).
JoinCondition = Tuple[List[str], List[str]]
# A join step as (join_table_parts, [conditions...]).
JoinStep = Tuple[List[str], List[JoinCondition]]

_IMPORTS = """# This file is auto-generated. Do not edit manually.

import datetime
import uuid
from decimal import Decimal
from typing import Any, Optional, List, Union
from pydantic import BaseModel
from tai42_mcp_dynamic_postgres.core.app import mcp_app
from tai42_mcp_dynamic_postgres.gen.templates.select_joined import select_joined_tmpl
from tai42_mcp_dynamic_postgres.gen.filters.models import WhereFilter
from tai42_mcp_dynamic_postgres.gen.order.models import OrderByItem

"""

_TOOL_TEMPLATE = '''
@mcp_app.tool(tags={{"postgres"}})
async def {func_name}(where: Optional[WhereFilter] = None, order_by: Optional[List[OrderByItem]] = None, limit: Optional[int] = None, offset: Optional[int] = None) -> List[{model_name}]:
    """
    Selects rows from joined tables: {tables_str}.

    Parameters:
        where: Optional filters to apply using `WhereFilter` on aliased columns (schema_table_column). For vector similarity (KNN), include in field filters like {{"aliased_vector": {{"knn": {{"query": [floats], "distance": "l2", "threshold": 0.5}}}}}}; this adds a distance-threshold predicate. To order results by distance, pass a KNN item in `order_by`. Combine with AND/OR as needed.
        order_by: Optional list of fields and directions to order by (using aliased columns), applied in the given order.
        limit: Optional maximum number of rows to return.
        offset: Optional number of leading rows to skip (pagination).

    Returns:
        List of `{model_name}` objects from the joined tables.
    """

    return await select_joined_tmpl(
        {select_items},
        {from_parts},
        {joins},
        {column_map},
        where,
        order_by,
        limit,
        offset,
        {model_name},
    )
'''


class SelectJoinedGen(BaseGen):
    """Emits one ``select_joined`` tool and model per configured table group."""

    def __init__(
        self,
        join_groups: Optional[List[List[str]]] = None,
        ignore_columns: Optional[List[str]] = None,
    ) -> None:
        """Configure the generator with the join groups and columns to exclude."""
        super().__init__(_FUNC_PREFIX, _IMPORTS, _TOOL_TEMPLATE, ignore_columns)
        self.join_groups = join_groups or []

    def tool_chunks(self, tables: Dict[str, TableInfo], fks: List[ForeignKey]) -> Iterable[Chunk]:
        """Yield one chunk per join group that projects at least one column."""
        for group in self.join_groups:
            chunk = self.generate_join_tool(group, tables, fks)
            if chunk is not None:
                yield chunk

    def joined_group_name(self, group: List[str], tables: Dict[str, TableInfo]) -> str:
        """Flattened ``schema_table1_table2_...`` base this group's tool and model derive from.

        Both the tool's ``func_name`` and its Pydantic model name build from this
        one string, so two groups producing the same value emit colliding names.
        """
        for t in group:
            if t not in tables:
                raise ValueError(f"Table {t} not found in schema.")
        schema_name = tables[group[0]].schema
        table_names = "_".join(tables[g].name for g in group)
        return f"{schema_name}_{table_names}"

    def find_join_condition(self, table_a: str, table_b: str, fks: List[ForeignKey]) -> Optional[List[JoinCondition]]:
        """Return the FK join predicate between two tables, or None if none relates them."""
        a_parts = table_a.split(".")
        b_parts = table_b.split(".")
        for fk_table, fk_cols, ref_table, ref_cols in fks:
            if fk_table == table_a and ref_table == table_b:
                return [([*a_parts, fc], [*b_parts, rc]) for fc, rc in zip(fk_cols, ref_cols, strict=True)]
            if fk_table == table_b and ref_table == table_a:
                return [([*b_parts, fc], [*a_parts, rc]) for fc, rc in zip(fk_cols, ref_cols, strict=True)]
        return None

    def _build_join_steps(self, group: List[str], fks: List[ForeignKey]) -> List[JoinStep]:
        """Order the joins, binding each table to a previously-joined one by FK."""
        joins: List[JoinStep] = []
        current_tables = [group[0]]
        for t in group[1:]:
            cond: Optional[List[JoinCondition]] = None
            for prev in current_tables:
                cond = self.find_join_condition(prev, t, fks)
                if cond:
                    break
            if not cond:
                raise ValueError(f"No foreign key relationship found to join {t} with any of {current_tables}")
            joins.append((t.split("."), cond))
            current_tables.append(t)
        return joins

    def _collect_join_columns(
        self, group: List[str], tables: Dict[str, TableInfo]
    ) -> Tuple[Dict[str, str], List[Tuple[List[str], str]], List[Tuple[str, str]]]:
        """Collect join columns under schema-qualified aliases.

        Aliasing keeps equal table names in different schemas (s1.users,
        s2.users) from colliding. Returns ``(column_map, select_items,
        model_columns)``; non-base-table columns are made Optional because an
        outer join can leave them null.
        """
        column_map: Dict[str, str] = {}
        select_items: List[Tuple[List[str], str]] = []
        model_columns: List[Tuple[str, str]] = []
        base_table = group[0]
        for t in group:
            if t not in tables:
                raise ValueError(f"Table {t} not found in schema.")
            table_info = tables[t]
            parts = t.split(".")
            for col in table_info.columns:
                if col.name in self.ignore_columns:
                    continue
                alias = f"{table_info.schema}_{table_info.name}_{col.name}"
                if alias in column_map:
                    raise ValueError(f"Join alias collision on {alias!r}; column selections are ambiguous.")
                column_map[alias] = f"{t}.{col.name}"
                select_items.append(([*parts, col.name], alias))
                typ = col.python_type
                if t != base_table and not typ.startswith("Optional["):
                    typ = f"Optional[{typ}]"
                model_columns.append((alias, typ))
        return column_map, select_items, model_columns

    def generate_join_tool(
        self, group: List[str], tables: Dict[str, TableInfo], fks: List[ForeignKey]
    ) -> Optional[Chunk]:
        """Build the joined model and tool code for ``group``, or None if it projects no columns."""
        if len(group) < 2:
            raise ValueError("Join group must have at least two tables.")

        joins = self._build_join_steps(group, fks)
        column_map, select_items, model_columns = self._collect_join_columns(group, tables)

        # All projected columns ignored -> empty SELECT and empty Row model, so the
        # tool would raise on every call. Skip rather than register a dead tool.
        if not select_items:
            return None

        from_parts = group[0].split(".")
        joined_group = self.joined_group_name(group, tables)
        model_name, model_code = sql_columns_to_pydantic_model(self.prefix, joined_group, model_columns)

        tool_code = self.template.format(
            func_name=self.func_name(joined_group),
            model_name=model_name,
            tables_str=", ".join(group),
            select_items=repr(select_items),
            from_parts=repr(from_parts),
            joins=repr(joins),
            column_map=repr(column_map),
        )

        return model_code, tool_code
