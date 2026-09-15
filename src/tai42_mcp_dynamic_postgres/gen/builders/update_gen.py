"""Generator for the per-table ``update`` tool and its partial-update model."""

from typing import List, Optional, Tuple

from tai42_mcp_dynamic_postgres.gen.builders.base_gen import Chunk, TableGen
from tai42_mcp_dynamic_postgres.gen.schema.codegen import sql_columns_to_pydantic_model
from tai42_mcp_dynamic_postgres.gen.schema.introspect import TableInfo

_FUNC_PREFIX = "update"

_IMPORTS = """# This file is auto-generated. Do not edit manually.

import datetime
import uuid
from decimal import Decimal
from typing import Any, Optional, List, Union
from pydantic import BaseModel
from tai42_mcp_dynamic_postgres.core.app import mcp_app
from tai42_mcp_dynamic_postgres.gen.templates.update import update_tmpl
from tai42_mcp_dynamic_postgres.gen.filters.models import WhereFilter

"""

_TOOL_TEMPLATE = '''
@mcp_app.tool(tags={{"postgres"}})
async def {func_name}(data: {model_name}, where: Optional[WhereFilter] = None) -> int:
    """
    Updates rows in the `{table}` table.

    Parameters:
        data: Partial `{model_name}` object with fields to update. At least one
              field must be set; an empty payload raises.
        where: Filters selecting the rows to update, using `WhereFilter`.
               A WHERE filter is required unless the server was started with
               --allow-unfiltered; updating with no filter otherwise raises.

    Returns:
        Number of rows updated in the `{table}` table.
    """
    return await update_tmpl(
        "{table}",
        {col_list},
        data,
        where,
        allow_unfiltered={allow_unfiltered},
        json_columns={json_columns},
    )
'''


class UpdateGen(TableGen):
    """Emits an ``update`` tool and partial-update model per writable table."""

    writable_only = True

    def __init__(self, ignore_columns: Optional[List[str]] = None, allow_unfiltered: bool = False) -> None:
        """Configure the generator, excluding ``ignore_columns`` and optionally allowing unfiltered updates."""
        super().__init__(_FUNC_PREFIX, _IMPORTS, _TOOL_TEMPLATE, ignore_columns)
        self.allow_unfiltered = allow_unfiltered

    def generate_tool(self, table_info: TableInfo) -> Optional[Chunk]:
        """Build the update model and tool code for ``table_info``, or None to skip it."""
        included = self.included(table_info)
        # Every updatable column ignored -> the tool can never set a field and
        # raises on every call. Skip rather than register a dead tool.
        if not included:
            return None

        # All update fields are optional (partial update).
        optional_columns: List[Tuple[str, str]] = []
        for col in included:
            typ = col.python_type if col.python_type.startswith("Optional[") else f"Optional[{col.python_type}]"
            optional_columns.append((col.name, typ))

        json_columns = [col.name for col in included if col.is_json]

        model_name, model_code = sql_columns_to_pydantic_model(self.prefix, table_info.qualified, optional_columns)

        tool_code = self.template.format(
            func_name=self.func_name(table_info.qualified),
            model_name=model_name,
            table=table_info.qualified,
            # WHERE may filter on any real column, even ones excluded from updates.
            col_list=repr(self.col_names(table_info)),
            allow_unfiltered=repr(self.allow_unfiltered),
            json_columns=repr(json_columns),
        )

        return model_code, tool_code
