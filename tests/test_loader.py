"""Unit tests for the dynamic tool loader: generating and importing tool files,
readonly pruning, select-joined and schema name-collision guards, deregistering
stale tools on reload, and partial-tool tracking on a mid-import raise. The
schema introspection and generated-module import are stubbed.
"""

import pytest


@pytest.fixture
def loader_env(monkeypatch, tmp_path):
    import tai42_mcp_dynamic_postgres.gen.builders.base_gen as base_gen
    import tai42_mcp_dynamic_postgres.gen.loader as loader_mod

    monkeypatch.setattr(base_gen, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(loader_mod, "OUTPUT_DIR", tmp_path)

    imported: list = []
    monkeypatch.setattr(loader_mod.importlib, "import_module", lambda name: imported.append(name))

    from schema_builders import col, schema, table

    users = table("public.users", [col("id", "int", has_default=True), col("name", "Optional[str]")], pk=["id"])
    tables = schema(users)

    async def fake_introspect():
        return tables, []

    monkeypatch.setattr(loader_mod, "introspect_schema", fake_introspect)
    return loader_mod, tmp_path, imported


async def test_loader_generates_and_imports_all_tools(loader_env):
    loader_mod, tmp_path, imported = loader_env
    await loader_mod.load_dynamic_tools(overwrite=True, readonly=False)
    names = {p.name for p in tmp_path.glob("*_tools.py")}
    assert names == {
        "select_joined_tools.py",
        "select_tools.py",
        "insert_tools.py",
        "update_tools.py",
        "delete_tools.py",
    }
    assert any(n.endswith("insert_tools") for n in imported)


async def test_generated_insert_tool_threads_model_fields_set(monkeypatch, tmp_path):
    # Exercise a real generated insert tool end to end: omitting a field vs.
    # sending it as null must reach insert_tmpl as distinct provided-field sets,
    # so an omitted column takes its DB default and an explicit null writes NULL.
    import sys

    from fastmcp import FastMCP

    import tai42_mcp_dynamic_postgres.core.app as app_mod
    import tai42_mcp_dynamic_postgres.gen.builders.base_gen as base_gen
    import tai42_mcp_dynamic_postgres.gen.loader as loader_mod
    from schema_builders import col, schema, table
    from tai42_mcp_dynamic_postgres import tools

    # A fresh app so decorating the generated tool never pollutes the singleton.
    monkeypatch.setattr(app_mod, "mcp_app", FastMCP())
    monkeypatch.setattr(base_gen, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(loader_mod, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(loader_mod, "_registered_tools", {})

    users = table("public.users", [col("id", "int", has_default=True), col("name", "Optional[str]")], pk=["id"])

    async def fake_introspect():
        return schema(users), []

    monkeypatch.setattr(loader_mod, "introspect_schema", fake_introspect)

    generated_modules = [
        f"tai42_mcp_dynamic_postgres.tools.{name}"
        for name in ("select_joined_tools", "select_tools", "insert_tools", "update_tools", "delete_tools")
    ]
    try:
        await loader_mod.load_dynamic_tools(overwrite=True, readonly=False)
        module = sys.modules["tai42_mcp_dynamic_postgres.tools.insert_tools"]

        captured: dict = {}

        async def fake_insert_tmpl(table_name, columns, values, raise_on_conflict=True, **kwargs):
            captured["values"] = values
            captured["provided_fields"] = kwargs["provided_fields"]
            return [1, 2]

        monkeypatch.setattr(module, "insert_tmpl", fake_insert_tmpl)

        row_model = module.InsertPublicUsersRow
        # First row omits id (falls back to the DB default); second sends id=None
        # explicitly (must be written as NULL).
        result = await module.insert_public_users([row_model(name="a"), row_model(id=None, name="b")])

        assert result == [1, 2]
        assert captured["values"] == [(None, "a"), (None, "b")]
        assert captured["provided_fields"] == [{"name"}, {"id", "name"}]
    finally:
        for mod in generated_modules:
            sys.modules.pop(mod, None)
        if str(tmp_path) in tools.__path__:
            tools.__path__.remove(str(tmp_path))


async def test_loader_readonly_prunes_write_tools(loader_env):
    loader_mod, tmp_path, imported = loader_env
    # First a read/write run leaves write tool files on disk.
    await loader_mod.load_dynamic_tools(overwrite=True, readonly=False)
    assert (tmp_path / "insert_tools.py").exists()

    # A later readonly run must remove the stale write tool files and never
    # import them.
    imported.clear()
    await loader_mod.load_dynamic_tools(overwrite=True, readonly=True)
    assert not (tmp_path / "insert_tools.py").exists()
    assert not (tmp_path / "update_tools.py").exists()
    assert not (tmp_path / "delete_tools.py").exists()
    assert (tmp_path / "select_tools.py").exists()
    assert not any(n.endswith(("insert_tools", "update_tools", "delete_tools")) for n in imported)


async def test_loader_select_joined_name_collision_raises(monkeypatch, tmp_path):
    # Two distinct --select-joined groups whose derived names flatten to the same
    # tool/model name must raise, not let FastMCP silently overwrite one join tool.
    import tai42_mcp_dynamic_postgres.gen.builders.base_gen as base_gen
    import tai42_mcp_dynamic_postgres.gen.loader as loader_mod
    from schema_builders import col, schema, table

    monkeypatch.setattr(base_gen, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(loader_mod, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(loader_mod, "_registered_tools", {})

    # "a" + "b_c" and "a_b" + "c" both flatten to select_joined_public_a_b_c.
    tables = schema(
        table("public.a", [col("id", "int", has_default=True)], pk=["id"]),
        table("public.b_c", [col("id", "int", has_default=True)], pk=["id"]),
        table("public.a_b", [col("id", "int", has_default=True)], pk=["id"]),
        table("public.c", [col("id", "int", has_default=True)], pk=["id"]),
    )

    async def fake_introspect():
        return tables, []

    monkeypatch.setattr(loader_mod, "introspect_schema", fake_introspect)

    with pytest.raises(ValueError, match="Select-joined name collision"):
        await loader_mod.load_dynamic_tools(
            overwrite=True,
            readonly=True,
            select_joined=[["public.a", "public.b_c"], ["public.a_b", "public.c"]],
        )


async def test_loader_select_joined_duplicate_group_raises(monkeypatch, tmp_path):
    # An exact duplicate group also collides on the derived name and must raise.
    import tai42_mcp_dynamic_postgres.gen.builders.base_gen as base_gen
    import tai42_mcp_dynamic_postgres.gen.loader as loader_mod
    from schema_builders import col, schema, table

    monkeypatch.setattr(base_gen, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(loader_mod, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(loader_mod, "_registered_tools", {})

    tables = schema(
        table("public.users", [col("id", "int", has_default=True), col("org_id", "Optional[int]")], pk=["id"]),
        table("public.orgs", [col("id", "int", has_default=True)], pk=["id"]),
    )

    async def fake_introspect():
        return tables, [("public.users", ["org_id"], "public.orgs", ["id"])]

    monkeypatch.setattr(loader_mod, "introspect_schema", fake_introspect)

    with pytest.raises(ValueError, match="Select-joined name collision"):
        await loader_mod.load_dynamic_tools(
            overwrite=True,
            readonly=True,
            select_joined=[["public.users", "public.orgs"], ["public.users", "public.orgs"]],
        )


async def test_loader_select_joined_distinct_groups_generate(monkeypatch, tmp_path):
    # Non-colliding groups still generate a join tool file without error.
    import tai42_mcp_dynamic_postgres.gen.builders.base_gen as base_gen
    import tai42_mcp_dynamic_postgres.gen.loader as loader_mod
    from schema_builders import col, schema, table

    monkeypatch.setattr(base_gen, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(loader_mod, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(loader_mod, "_registered_tools", {})
    monkeypatch.setattr(loader_mod.importlib, "import_module", lambda name: None)

    tables = schema(
        table("public.users", [col("id", "int", has_default=True), col("org_id", "Optional[int]")], pk=["id"]),
        table("public.orgs", [col("id", "int", has_default=True)], pk=["id"]),
        table("public.teams", [col("id", "int", has_default=True), col("org_id", "Optional[int]")], pk=["id"]),
    )
    fks = [
        ("public.users", ["org_id"], "public.orgs", ["id"]),
        ("public.teams", ["org_id"], "public.orgs", ["id"]),
    ]

    async def fake_introspect():
        return tables, fks

    monkeypatch.setattr(loader_mod, "introspect_schema", fake_introspect)

    await loader_mod.load_dynamic_tools(
        overwrite=True,
        readonly=True,
        select_joined=[["public.users", "public.orgs"], ["public.teams", "public.orgs"]],
    )
    code = (tmp_path / "select_joined_tools.py").read_text()
    assert "async def select_joined_public_users_orgs" in code
    assert "async def select_joined_public_teams_orgs" in code


async def test_loader_deregisters_write_tools_on_readonly_reload(monkeypatch, tmp_path):
    # A second load in the same process (e.g. an embedding host reloading in
    # --readonly) must remove the previously-registered write tools from the app,
    # not merely delete their files on disk.
    import sys

    from fastmcp import FastMCP

    import tai42_mcp_dynamic_postgres.core.app as app_mod
    import tai42_mcp_dynamic_postgres.gen.builders.base_gen as base_gen
    import tai42_mcp_dynamic_postgres.gen.loader as loader_mod
    from schema_builders import col, schema, table
    from tai42_mcp_dynamic_postgres import tools

    monkeypatch.setattr(base_gen, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(loader_mod, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(loader_mod, "_registered_tools", {})

    # A fresh app so the real singleton (and other tests) is never polluted; the
    # generated modules bind to it via ``from core.app import mcp_app``.
    monkeypatch.setattr(app_mod, "mcp_app", FastMCP())

    users = table("public.users", [col("id", "int", has_default=True), col("name", "Optional[str]")], pk=["id"])

    async def fake_introspect():
        return schema(users), []

    monkeypatch.setattr(loader_mod, "introspect_schema", fake_introspect)

    generated_modules = [
        f"tai42_mcp_dynamic_postgres.tools.{name}"
        for name in ("select_joined_tools", "select_tools", "insert_tools", "update_tools", "delete_tools")
    ]

    async def live_names():
        return {t.name for t in await app_mod.mcp_app.local_provider.list_tools()}

    try:
        await loader_mod.load_dynamic_tools(overwrite=True, readonly=False)
        names = await live_names()
        assert "insert_public_users" in names
        assert "update_public_users" in names
        assert "delete_public_users" in names
        assert "select_public_users" in names

        await loader_mod.load_dynamic_tools(overwrite=True, readonly=True)
        names = await live_names()
        assert not any(n.startswith(("insert_", "update_", "delete_")) for n in names)
        assert "select_public_users" in names
    finally:
        for mod in generated_modules:
            sys.modules.pop(mod, None)
        if str(tmp_path) in tools.__path__:
            tools.__path__.remove(str(tmp_path))


async def test_loader_tracks_partial_tools_when_reload_raises(monkeypatch, tmp_path):
    # A module that registers some @mcp_app.tool decorators then raises mid-import
    # leaves live tools; _registered_tools must record them so a later run can
    # deregister them, not leak an untracked write tool.
    import sys

    from fastmcp import FastMCP

    import tai42_mcp_dynamic_postgres.core.app as app_mod
    import tai42_mcp_dynamic_postgres.gen.builders.base_gen as base_gen
    import tai42_mcp_dynamic_postgres.gen.builders.select_gen as select_gen_mod
    import tai42_mcp_dynamic_postgres.gen.loader as loader_mod
    from schema_builders import col, schema, table
    from tai42_mcp_dynamic_postgres import tools

    monkeypatch.setattr(base_gen, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(loader_mod, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(loader_mod, "_registered_tools", {})
    monkeypatch.setattr(app_mod, "mcp_app", FastMCP())

    users = table("public.users", [col("id", "int", has_default=True), col("name", "Optional[str]")], pk=["id"])

    async def fake_introspect():
        return schema(users), []

    monkeypatch.setattr(loader_mod, "introspect_schema", fake_introspect)

    # Make the select_tools module register one tool and then raise mid-import.
    def boom_generate_file(self, tables, fks):
        self.output_path.write_text(
            "from tai42_mcp_dynamic_postgres.core.app import mcp_app\n"
            "\n"
            "@mcp_app.tool\n"
            "def partial_tool_select():\n"
            "    return None\n"
            "\n"
            "raise RuntimeError('boom mid select_tools import')\n"
        )

    monkeypatch.setattr(select_gen_mod.SelectGen, "generate_file", boom_generate_file)

    generated_modules = [f"tai42_mcp_dynamic_postgres.tools.{name}" for name in ("select_joined_tools", "select_tools")]

    # Isolate the import search path and module cache so the real import reads
    # this test's boom file from tmp_path (other tests leave stale tools.__path__
    # entries whose valid select_tools.py would otherwise be found first).
    original_path = list(tools.__path__)
    tools.__path__[:] = []
    for mod in generated_modules:
        sys.modules.pop(mod, None)

    try:
        with pytest.raises(RuntimeError, match="boom mid select_tools import"):
            await loader_mod.load_dynamic_tools(overwrite=True, readonly=True)

        # The partially-registered tool is live AND tracked despite the raise.
        live = {t.name for t in await app_mod.mcp_app.local_provider.list_tools()}
        assert "partial_tool_select" in live
        assert loader_mod._registered_tools["select_tools"] == {"partial_tool_select"}

        # Tracked means removable: a later run can deregister the partial tool.
        loader_mod._deregister_module_tools("select_tools")
        live_after = {t.name for t in await app_mod.mcp_app.local_provider.list_tools()}
        assert "partial_tool_select" not in live_after
    finally:
        for mod in generated_modules:
            sys.modules.pop(mod, None)
        tools.__path__[:] = original_path


async def test_loader_raises_on_name_collision(loader_env, monkeypatch):
    loader_mod, _tmp_path, _imported = loader_env
    from schema_builders import col, schema, table

    colliding = schema(
        table("a_b.c", [col("id", "int")]),
        table("a.b_c", [col("id", "int")]),
    )

    async def fake_introspect():
        return colliding, []

    monkeypatch.setattr(loader_mod, "introspect_schema", fake_introspect)
    with pytest.raises(ValueError, match="collision"):
        await loader_mod.load_dynamic_tools(overwrite=True, readonly=True)
