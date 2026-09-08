import importlib
import sys
from typing import Dict, List, Optional, Set

from tai42_mcp_dynamic_postgres import tools
from tai42_mcp_dynamic_postgres.core import app as app_module
from tai42_mcp_dynamic_postgres.gen.builders.base_gen import OUTPUT_DIR, TOOLS_SUFFIX, BaseGen
from tai42_mcp_dynamic_postgres.gen.builders.delete_gen import DeleteGen
from tai42_mcp_dynamic_postgres.gen.builders.insert_gen import InsertGen
from tai42_mcp_dynamic_postgres.gen.builders.select_gen import SelectGen
from tai42_mcp_dynamic_postgres.gen.builders.select_joined_gen import SelectJoinedGen
from tai42_mcp_dynamic_postgres.gen.builders.update_gen import UpdateGen
from tai42_mcp_dynamic_postgres.gen.schema.introspect import TableInfo, introspect_schema

# Tool names this loader registered on the shared app, keyed by generated-module
# name. Tracked so a repeat ``load_dynamic_tools`` call can refresh or deregister a
# module's tools instead of leaking stale ones.
_registered_tools: Dict[str, Set[str]] = {}


async def _live_tool_names() -> Set[str]:
    """Names of every tool currently registered on the shared app."""
    return {tool.name for tool in await app_module.mcp_app.local_provider.list_tools()}


def _deregister_module_tools(module_name: str) -> None:
    """Remove and forget the tools previously registered by ``module_name``."""
    provider = app_module.mcp_app.local_provider
    for tool_name in _registered_tools.pop(module_name, set()):
        provider.remove_tool(tool_name)


def _check_name_collisions(tables: Dict[str, TableInfo]) -> None:
    """Fail loudly if two ``schema.table`` names flatten to the same tool name.

    Tool and model names replace ``.`` with ``_``, so ``a_b.c`` and ``a.b_c``
    both become ``a_b_c`` and one tool would silently shadow the other.
    """
    seen: Dict[str, str] = {}
    for qualified in tables:
        flat = qualified.replace(".", "_")
        if flat in seen:
            raise ValueError(
                f"Table name collision: {seen[flat]!r} and {qualified!r} both flatten to {flat!r}, "
                f"which would generate colliding tool and model names. Rename one of them, or expose "
                f"them under non-colliding schema/table names."
            )
        seen[flat] = qualified


def _check_select_joined_name_collisions(gen: SelectJoinedGen, tables: Dict[str, TableInfo]) -> None:
    """Fail loudly if two ``--select-joined`` groups derive the same tool/model name.

    Flattened join names like ``a,b_c`` and ``a_b,c`` can collapse together, and
    FastMCP overwrites a duplicate tool name (warn, no raise), silently shadowing
    one join tool.
    """
    seen: Dict[str, List[str]] = {}
    for group in gen.join_groups:
        name = gen.func_name(gen.joined_group_name(group, tables))
        if name in seen:
            raise ValueError(
                f"Select-joined name collision: groups {seen[name]} and {group} both derive the tool "
                f"and model name {name!r}, so one join tool would silently overwrite the other. Rename "
                f"or restructure one of the groups so their flattened schema_table names differ."
            )
        seen[name] = group


async def load_dynamic_tools(
    overwrite: bool = True,
    readonly: bool = False,
    allow_unfiltered: bool = False,
    ignore_insert_columns: Optional[List[str]] = None,
    ignore_select_columns: Optional[List[str]] = None,
    ignore_update_columns: Optional[List[str]] = None,
    ignore_select_joined_columns: Optional[List[str]] = None,
    select_joined: Optional[List[List[str]]] = None,
) -> None:
    gen_list: List[BaseGen] = []
    gen_list.append(SelectJoinedGen(select_joined, ignore_select_joined_columns))
    gen_list.append(SelectGen(ignore_select_columns))

    if not readonly:
        gen_list.append(InsertGen(ignore_insert_columns))
        gen_list.append(UpdateGen(ignore_update_columns, allow_unfiltered=allow_unfiltered))
        gen_list.append(DeleteGen(allow_unfiltered=allow_unfiltered))

    to_generate = gen_list if overwrite else [gen for gen in gen_list if not gen.is_exists]
    if to_generate:
        tables, fks = await introspect_schema()
        _check_name_collisions(tables)
        for gen in to_generate:
            if isinstance(gen, SelectJoinedGen):
                _check_select_joined_name_collisions(gen, tables)
        for gen in to_generate:
            gen.generate_file(tables, fks)

    # Prune previously-generated tool files not part of this load, so a later
    # --readonly run never serves stale write tools.
    expected = {f"{gen.module_name}.py" for gen in gen_list}
    for path in OUTPUT_DIR.glob(f"*{TOOLS_SUFFIX}.py"):
        if path.name not in expected:
            path.unlink()

    # Make the writable output dir importable, then import exactly this load's
    # modules (never a blanket walk of whatever files happen to be on disk).
    if str(OUTPUT_DIR) not in tools.__path__:
        tools.__path__.append(str(OUTPUT_DIR))

    # Deregister tools of any module this load no longer generates, so the app
    # stops serving a tool whose generated file was just pruned.
    expected_modules = {gen.module_name for gen in gen_list}
    for stale_module in [name for name in _registered_tools if name not in expected_modules]:
        _deregister_module_tools(stale_module)

    for gen in gen_list:
        module_name = f"{tools.__name__}.{gen.module_name}"
        # Drop this module's previously-registered tools first so a reload
        # re-registers them cleanly instead of warning on a duplicate name.
        _deregister_module_tools(gen.module_name)
        before = await _live_tool_names()
        try:
            existing = sys.modules.get(module_name)
            if existing is not None:
                # Already imported in this process: reload so a regenerated file
                # (new schema or flags) actually re-registers, rather than serving
                # a stale cached module.
                importlib.reload(existing)
            else:
                importlib.import_module(module_name)
        except ImportError as e:
            raise ImportError(f"Failed to import generated tool module {module_name!r}: {e}") from e
        finally:
            # Record the tools this module added, in ``finally`` so tools registered
            # before a mid-module raise are still tracked and removable, not leaked.
            _registered_tools[gen.module_name] = await _live_tool_names() - before
