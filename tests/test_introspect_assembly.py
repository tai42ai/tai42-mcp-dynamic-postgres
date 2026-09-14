"""Unit tests for introspection assembly: ``introspect_schema`` builds the
table/column/primary-key/enum/kind structures and reconstructs (composite and
same-named) foreign keys from cursor result rows. The database cursor is stubbed.
"""

import pytest


class _FakeIntrospectCursor:
    def __init__(self, results):
        self._results = list(results)
        self._i = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params=None):
        return None

    async def fetchall(self):
        result = self._results[self._i]
        self._i += 1
        return result


def _patch_introspect_cursor(monkeypatch, results):
    import tai42_mcp_dynamic_postgres.gen.schema.introspect as introspect_mod

    def fake_cursor(*args, **kwargs):
        return _FakeIntrospectCursor(results)

    monkeypatch.setattr(introspect_mod, "cursor", fake_cursor)


async def test_introspect_assembles_tables_pk_enum_kinds(monkeypatch):
    from tai42_mcp_dynamic_postgres.gen.schema.introspect import introspect_schema

    enum_rows = [("mood",)]
    column_rows = [
        ("public", "t", "r", "id", "integer", True, True),
        ("public", "t", "r", "name", "text", False, False),
        ("public", "t", "r", "feeling", "mood", False, False),
        ("public", "t", "r", "meta", "jsonb", False, False),
        ("public", "t", "r", "tags", "text[]", False, False),
        ("public", "t", "r", "created", "timestamptz", False, True),
        ("public", "v_t", "v", "id", "integer", False, False),
    ]
    pk_rows = [("public", "t", "id")]
    fk_rows = []
    _patch_introspect_cursor(monkeypatch, [enum_rows, column_rows, pk_rows, fk_rows])

    tables, fks = await introspect_schema()

    t = tables["public.t"]
    assert t.kind == "r"
    assert t.primary_key == ["id"]
    by_name = {c.name: c for c in t.columns}
    assert by_name["id"].has_default is True
    assert by_name["id"].nullable is False
    assert by_name["feeling"].python_type == "Optional[str]"  # enum
    assert by_name["meta"].is_json is True
    assert by_name["meta"].python_type == "Optional[Any]"
    assert by_name["tags"].python_type == "Optional[List[str]]"
    assert by_name["created"].has_default is True

    assert tables["public.v_t"].kind == "v"
    assert tables["public.v_t"].writable is False
    assert fks == []


async def test_introspect_assembles_composite_fk(monkeypatch):
    from tai42_mcp_dynamic_postgres.gen.schema.introspect import introspect_schema

    column_rows = [
        ("public", "t", "r", "a", "integer", True, False),
        ("public", "t", "r", "b", "integer", True, False),
    ]
    fk_rows = [
        ("fk1", "public", "t", "a", "public", "p", "x"),
        ("fk1", "public", "t", "b", "public", "p", "y"),
    ]
    _patch_introspect_cursor(monkeypatch, [[], column_rows, [], fk_rows])

    _, fks = await introspect_schema()
    assert ("public.t", ["a", "b"], "public.p", ["x", "y"]) in fks


async def test_introspect_separates_same_named_fks_on_different_tables(monkeypatch):
    # A constraint name is unique only per table in PostgreSQL, so two different
    # tables can each own a FK named ``parent_fk``. They must reconstruct as two
    # independent single-column keys, not merge into one corrupted composite key.
    from tai42_mcp_dynamic_postgres.gen.builders.select_joined_gen import SelectJoinedGen
    from tai42_mcp_dynamic_postgres.gen.schema.introspect import introspect_schema

    column_rows = [
        ("public", "parents", "r", "id", "integer", True, True),
        ("public", "a", "r", "id", "integer", True, True),
        ("public", "a", "r", "parent_id", "integer", False, False),
        ("public", "b", "r", "id", "integer", True, True),
        ("public", "b", "r", "parent_id", "integer", False, False),
    ]
    pk_rows = [("public", "parents", "id"), ("public", "a", "id"), ("public", "b", "id")]
    # Both constraints share the name ``parent_fk`` but belong to different tables.
    fk_rows = [
        ("parent_fk", "public", "a", "parent_id", "public", "parents", "id"),
        ("parent_fk", "public", "b", "parent_id", "public", "parents", "id"),
    ]
    _patch_introspect_cursor(monkeypatch, [[], column_rows, pk_rows, fk_rows])

    _, fks = await introspect_schema()

    # Each table keeps its own single-column FK; neither is dropped or corrupted.
    assert ("public.a", ["parent_id"], "public.parents", ["id"]) in fks
    assert ("public.b", ["parent_id"], "public.parents", ["id"]) in fks
    assert len(fks) == 2

    # A join built from each FK references only that table's own columns.
    gen = SelectJoinedGen()
    cond_a = gen.find_join_condition("public.a", "public.parents", fks)
    assert cond_a == [(["public", "a", "parent_id"], ["public", "parents", "id"])]
    cond_b = gen.find_join_condition("public.b", "public.parents", fks)
    assert cond_b == [(["public", "b", "parent_id"], ["public", "parents", "id"])]


async def test_introspect_rejects_unsafe_identifier(monkeypatch):
    from tai42_mcp_dynamic_postgres.gen.schema.introspect import introspect_schema

    column_rows = [("public", "t", "r", "bad name", "integer", False, False)]
    _patch_introspect_cursor(monkeypatch, [[], column_rows, [], []])
    with pytest.raises(ValueError, match="Unsupported column name"):
        await introspect_schema()
