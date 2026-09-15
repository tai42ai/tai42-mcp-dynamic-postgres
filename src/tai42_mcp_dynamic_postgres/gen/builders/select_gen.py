"""Generator for the per-table ``select`` tool and its row model."""

from typing import List, Optional

from tai42_mcp_dynamic_postgres.gen.builders.base_gen import Chunk, TableGen
from tai42_mcp_dynamic_postgres.gen.schema.codegen import sql_columns_to_pydantic_model
from tai42_mcp_dynamic_postgres.gen.schema.introspect import TableInfo

_FUNC_PREFIX = "select"

_IMPORTS = """# This file is auto-generated. Do not edit manually.

import datetime
import uuid
from decimal import Decimal
from typing import Any, Optional, List, Union
from pydantic import BaseModel
from tai42_mcp_dynamic_postgres.core.app import mcp_app
from tai42_mcp_dynamic_postgres.gen.templates.select import select_tmpl
from tai42_mcp_dynamic_postgres.gen.filters.models import WhereFilter
from tai42_mcp_dynamic_postgres.gen.order.models import OrderByItem

"""

_TOOL_TEMPLATE = '''
@mcp_app.tool(tags={{"postgres"}})
async def {func_name}(where: Optional[WhereFilter] = None, order_by: Optional[List[OrderByItem]] = None, limit: Optional[int] = None, offset: Optional[int] = None) -> List[{model_name}]:
    """
    Selects rows from the `{table}` table.

    Parameters:
        where: Optional filters to apply using `WhereFilter`. For vector similarity (KNN), include in field filters like {{"vector_field": {{"knn": {{"query": [floats], "distance": "l2", "threshold": 0.5}}}}}}; this adds a distance-threshold predicate. To order results by distance, pass a KNN item in `order_by`. Combine with AND/OR as needed.
        order_by: Optional list of fields and directions to order by, applied in the given order (use a KNN item to order by vector distance).
        limit: Optional maximum number of rows to return.
        offset: Optional number of leading rows to skip (pagination).

    Returns:
        List of `{model_name}` objects from the `{table}` table.
    """

    return await select_tmpl("{table}", {col_list}, where, order_by, limit, offset, {model_name}, select_columns={select_columns})
'''


class SelectGen(TableGen):
    """Emits a ``select`` tool and row model per relation."""

    def __init__(self, ignore_columns: Optional[List[str]] = None) -> None:
        """Configure the generator, excluding ``ignore_columns`` from the projection."""
        super().__init__(_FUNC_PREFIX, _IMPORTS, _TOOL_TEMPLATE, ignore_columns)

    def generate_tool(self, table_info: TableInfo) -> Optional[Chunk]:
        """Build the select model and tool code for ``table_info``, or None to skip it."""
        included = self.included(table_info)
        # All columns ignored -> empty projection and empty Row model, so the tool
        # would raise on every call. Skip rather than register a dead tool.
        if not included:
            return None

        model_columns = [(col.name, col.python_type) for col in included]
        model_name, model_code = sql_columns_to_pydantic_model(self.prefix, table_info.qualified, model_columns)

        tool_code = self.template.format(
            func_name=self.func_name(table_info.qualified),
            model_name=model_name,
            table=table_info.qualified,
            # WHERE/ORDER BY may reference any real column, even ones excluded
            # from the returned model, so the allowlist uses the full set.
            col_list=repr(self.col_names(table_info)),
            # The SQL projection lists only exposed columns, so ignored columns
            # are never fetched from the database (not just dropped from the model).
            select_columns=repr([col.name for col in included]),
        )

        return model_code, tool_code
