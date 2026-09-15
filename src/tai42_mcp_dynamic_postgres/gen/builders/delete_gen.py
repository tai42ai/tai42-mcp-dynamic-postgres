"""Generator for the per-table ``delete`` tool."""

from typing import Optional

from tai42_mcp_dynamic_postgres.gen.builders.base_gen import Chunk, TableGen
from tai42_mcp_dynamic_postgres.gen.schema.introspect import TableInfo

_FUNC_PREFIX = "delete"

_IMPORTS = """# This file is auto-generated. Do not edit manually.

from typing import Optional
from tai42_mcp_dynamic_postgres.core.app import mcp_app
from tai42_mcp_dynamic_postgres.gen.templates.delete import delete_tmpl
from tai42_mcp_dynamic_postgres.gen.filters.models import WhereFilter

"""

_TOOL_TEMPLATE = '''
@mcp_app.tool(tags={{"postgres"}})
async def {func_name}(where: Optional[WhereFilter] = None) -> int:
    """
    Deletes rows from the `{table}` table.

    Parameters:
        where: Filters selecting the rows to delete, using `WhereFilter`.
               A WHERE filter is required unless the server was started with
               --allow-unfiltered; deleting with no filter otherwise raises.

    Returns:
        Number of rows deleted from the `{table}` table.
    """

    return await delete_tmpl("{table}", {col_list}, where, allow_unfiltered={allow_unfiltered})
'''


class DeleteGen(TableGen):
    """Emits a ``delete`` tool per writable table."""

    writable_only = True

    def __init__(self, allow_unfiltered: bool = False) -> None:
        """Configure the generator, optionally allowing deletes with no WHERE filter."""
        super().__init__(_FUNC_PREFIX, _IMPORTS, _TOOL_TEMPLATE)
        self.allow_unfiltered = allow_unfiltered

    def generate_tool(self, table_info: TableInfo) -> Optional[Chunk]:
        """Build the delete tool code for ``table_info`` (no model)."""
        tool_code = self.template.format(
            func_name=self.func_name(table_info.qualified),
            table=table_info.qualified,
            # DELETE has no body columns, but WHERE may filter on any real column.
            col_list=repr(self.col_names(table_info)),
            allow_unfiltered=repr(self.allow_unfiltered),
        )
        return "", tool_code  # No model needed for delete
