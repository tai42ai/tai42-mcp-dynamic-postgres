"""Generator for the per-table ``insert`` tool and its row model."""

from typing import List, Optional, Tuple

from tai42_mcp_dynamic_postgres.gen.builders.base_gen import Chunk, TableGen
from tai42_mcp_dynamic_postgres.gen.schema.codegen import sql_columns_to_pydantic_model
from tai42_mcp_dynamic_postgres.gen.schema.introspect import TableInfo

_FUNC_PREFIX = "insert"

_IMPORTS = """# This file is auto-generated. Do not edit manually.

import datetime
import uuid
from decimal import Decimal
from typing import Any, Optional, List, Union
from pydantic import BaseModel
from tai42_mcp_dynamic_postgres.core.app import mcp_app
from tai42_mcp_dynamic_postgres.gen.templates.insert import insert_tmpl

"""

_TOOL_TEMPLATE = '''
@mcp_app.tool(tags={{"postgres"}})
async def {func_name}(params: List[{model_name}], raise_on_conflict: bool = True) -> {return_type}:
    """
    Inserts multiple rows into the `{table}` table.

    Parameters:
        params: List of `{model_name}` objects in the order: {doc_params}
        raise_on_conflict: If True, raise an error on unique constraint conflict.
                           If False, conflicting rows will be ignored (ON CONFLICT DO NOTHING).

    Returns:
        {return_doc}
    """

    rows = params or []
    values = [({args},) for row in rows]
    # The set of fields each caller actually supplied, so an omitted column
    # takes its database DEFAULT while an explicit null is written as SQL NULL.
    provided_fields = [row.model_fields_set for row in rows]

    return await insert_tmpl(
        "{table}",
        {col_list},
        values,
        raise_on_conflict,
        default_columns={default_columns},
        json_columns={json_columns},
        pk_columns={pk_columns},
        provided_fields=provided_fields,
    )
'''


def _strip_optional(annotation: str) -> str:
    if annotation.startswith("Optional[") and annotation.endswith("]"):
        return annotation[len("Optional[") : -1]
    return annotation


class InsertGen(TableGen):
    """Emits an ``insert`` tool and row model per writable table."""

    writable_only = True

    def __init__(self, ignore_columns: Optional[List[str]] = None) -> None:
        """Configure the generator, excluding ``ignore_columns`` from the insert model."""
        super().__init__(_FUNC_PREFIX, _IMPORTS, _TOOL_TEMPLATE, ignore_columns)

    def _return_type_and_doc(self, table_info: TableInfo) -> Tuple[str, str]:
        pk = table_info.primary_key
        if not pk:
            return "int", f"Number of rows inserted into the `{table_info.qualified}` table (no primary key to return)."
        types = {col.name: _strip_optional(col.python_type) for col in table_info.columns}
        if len(pk) == 1:
            if pk[0] not in types:
                # A PK column is always among the introspected columns; a missing
                # one signals inconsistent introspection, so fail loud, not ``Any``.
                raise ValueError(
                    f"Primary-key column {pk[0]!r} of table {table_info.qualified!r} is not among its "
                    f"introspected columns {sorted(types)}; cannot determine the insert return type."
                )
            pk_type = types[pk[0]]
            return f"List[{pk_type}]", f"List of inserted `{pk[0]}` values from the `{table_info.qualified}` table."
        cols = ", ".join(pk)
        doc = f"List of inserted primary-key lists ({cols}) from the `{table_info.qualified}` table."
        return "List[List[Any]]", doc

    def generate_tool(self, table_info: TableInfo) -> Optional[Chunk]:
        """Build the insert model and tool code for ``table_info``, or None to skip it."""
        insert_columns = self.included(table_info)
        # An all-columns-ignored table has nothing to insert via the tool; skip
        # it rather than emitting an empty, invalid values tuple.
        if not insert_columns:
            return None

        names = [col.name for col in insert_columns]
        default_columns = [col.name for col in insert_columns if col.has_default]
        json_columns = [col.name for col in insert_columns if col.is_json]

        # Columns with a default (incl. serial/identity) and nullable columns are
        # omittable, so the database default / NULL applies when not supplied.
        model_columns: List[Tuple[str, str]] = []
        for col in insert_columns:
            typ = col.python_type
            if (col.has_default or col.nullable) and not typ.startswith("Optional["):
                typ = f"Optional[{typ}]"
            model_columns.append((col.name, typ))

        model_name, model_code = sql_columns_to_pydantic_model(self.prefix, table_info.qualified, model_columns)
        return_type, return_doc = self._return_type_and_doc(table_info)

        tool_code = self.template.format(
            func_name=self.func_name(table_info.qualified),
            model_name=model_name,
            table=table_info.qualified,
            doc_params=", ".join(names),
            # Trailing comma in the template keeps a single-column row a tuple.
            args=", ".join(f"row.{col}" for col in names),
            col_list=repr(names),
            default_columns=repr(default_columns),
            json_columns=repr(json_columns),
            pk_columns=repr(table_info.primary_key),
            return_type=return_type,
            return_doc=return_doc,
        )

        return model_code, tool_code
