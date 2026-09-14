"""Unit tests for SQL composition and bound-param lists in the DML templates.

Execution is stubbed (fake pool/cursor), so these run without a database and
assert the exact SQL and the exact ordered parameter list.
"""

from typing import Any, ClassVar, Optional

import pytest
from psycopg.types.json import Json
from pydantic import BaseModel

from tai42_mcp_dynamic_postgres.gen.filters.models import WhereFilter
from tai42_mcp_dynamic_postgres.gen.order.models import OrderByItem


class FakeCursor:
    def __init__(self, cap, rows, rowcount):
        self._cap = cap
        self._rows = rows
        self.rowcount = rowcount

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, query, params=None):
        self._cap["query"] = query
        self._cap["params"] = params

    async def fetchall(self):
        return self._rows


class FakeConn:
    def __init__(self, cursor):
        self._cursor = cursor

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self, *args, **kwargs):
        return self._cursor


class FakePool:
    def __init__(self, conn):
        self._conn = conn

    def connection(self):
        return self._conn


@pytest.fixture
def capture(monkeypatch):
    def install(rows=None, rowcount=0):
        cap: dict = {"query": None, "params": None}
        cur = FakeCursor(cap, rows or [], rowcount)
        pool = FakePool(FakeConn(cur))

        async def fake_pool():
            return pool

        def fake_cursor(*args, **kwargs):
            return cur

        import tai42_mcp_dynamic_postgres.gen.templates.delete as delete_mod
        import tai42_mcp_dynamic_postgres.gen.templates.insert as insert_mod
        import tai42_mcp_dynamic_postgres.gen.templates.select_runner as select_runner_mod
        import tai42_mcp_dynamic_postgres.gen.templates.update as update_mod

        monkeypatch.setattr(insert_mod, "get_connection_pool", fake_pool)
        monkeypatch.setattr(update_mod, "get_connection_pool", fake_pool)
        monkeypatch.setattr(delete_mod, "get_connection_pool", fake_pool)
        monkeypatch.setattr(select_runner_mod, "cursor", fake_cursor)
        return cap

    return install


def rendered(cap):
    return cap["query"].as_string(None)


# --- insert -----------------------------------------------------------------


async def test_insert_exact_sql_and_params(capture):
    from tai42_mcp_dynamic_postgres.gen.templates.insert import insert_tmpl

    cap = capture(rows=[(1,), (2,)])
    result = await insert_tmpl("public.items", ["name", "qty"], [("a", 1), ("b", 2)], pk_columns=["id"])
    assert rendered(cap) == ('INSERT INTO "public"."items" ("name", "qty") VALUES (%s, %s), (%s, %s)  RETURNING "id"')
    assert cap["params"] == ["a", 1, "b", 2]
    assert result == [1, 2]


async def test_insert_default_column_emits_default_keyword(capture):
    from tai42_mcp_dynamic_postgres.gen.templates.insert import insert_tmpl

    cap = capture(rows=[(5,)])
    # id omitted (not in the provided set) with a DB default -> DEFAULT keyword.
    await insert_tmpl(
        "public.t",
        ["id", "name"],
        [(None, "a")],
        default_columns=["id"],
        pk_columns=["id"],
        provided_fields=[{"name"}],
    )
    assert "(DEFAULT, %s)" in rendered(cap)
    assert cap["params"] == ["a"]


async def test_insert_explicit_null_overrides_default(capture):
    from tai42_mcp_dynamic_postgres.gen.templates.insert import insert_tmpl

    cap = capture(rows=[(5,)])
    # id explicitly sent as None (in the provided set) -> NULL binds, not DEFAULT,
    # even though id has a database default.
    await insert_tmpl(
        "public.t",
        ["id", "name"],
        [(None, "a")],
        default_columns=["id"],
        pk_columns=["id"],
        provided_fields=[{"id", "name"}],
    )
    assert "DEFAULT" not in rendered(cap)
    assert rendered(cap).count("%s") == 2
    assert cap["params"] == [None, "a"]


async def test_insert_provided_fields_none_binds_all(capture):
    from tai42_mcp_dynamic_postgres.gen.templates.insert import insert_tmpl

    cap = capture(rows=[(5,)])
    # Without provided_fields every listed column is treated as supplied, so a
    # default column is never emitted as DEFAULT.
    await insert_tmpl("public.t", ["id", "name"], [(3, "a")], default_columns=["id"], pk_columns=["id"])
    assert "DEFAULT" not in rendered(cap)
    assert cap["params"] == [3, "a"]


async def test_insert_json_column_wrapped_array_not_wrapped(capture):
    from tai42_mcp_dynamic_postgres.gen.templates.insert import insert_tmpl

    cap = capture(rows=[(1,)])
    await insert_tmpl("public.d", ["meta"], [({"k": 1},)], json_columns=["meta"], pk_columns=["id"])
    assert isinstance(cap["params"][0], Json)

    cap = capture(rows=[(1,)])
    await insert_tmpl("public.d", ["tags"], [(["a", "b"],)], json_columns=[], pk_columns=["id"])
    # Array value is passed natively; psycopg adapts it (never JSON-wrapped).
    assert cap["params"] == [["a", "b"]]


async def test_insert_no_pk_returns_rowcount(capture):
    from tai42_mcp_dynamic_postgres.gen.templates.insert import insert_tmpl

    cap = capture(rowcount=3)
    result = await insert_tmpl("public.log", ["msg"], [("x",)], pk_columns=[])
    assert "RETURNING" not in rendered(cap)
    assert result == 3


async def test_insert_composite_pk_returns_tuples(capture):
    from tai42_mcp_dynamic_postgres.gen.templates.insert import insert_tmpl

    cap = capture(rows=[(1, 2)])
    result = await insert_tmpl("public.link", ["a", "b"], [(1, 2)], pk_columns=["a", "b"])
    assert 'RETURNING "a", "b"' in rendered(cap)
    assert result == [[1, 2]]


async def test_insert_empty_values_short_circuits(capture):
    from tai42_mcp_dynamic_postgres.gen.templates.insert import insert_tmpl

    capture()
    assert await insert_tmpl("public.t", ["a"], [], pk_columns=["id"]) == []
    assert await insert_tmpl("public.t", ["a"], [], pk_columns=[]) == 0


async def test_insert_rogue_column_key_is_quoted(capture):
    from tai42_mcp_dynamic_postgres.gen.templates.insert import insert_tmpl

    cap = capture(rows=[(1,)])
    await insert_tmpl("public.t", ["ok", 'evil"; DROP TABLE x; --'], [(1, 2)], pk_columns=["id"])
    # The rogue name is emitted only as a quoted identifier (inner quote doubled).
    assert '"evil""; DROP TABLE x; --"' in rendered(cap)


# --- update -----------------------------------------------------------------


class _Upd(BaseModel):
    name: Optional[str] = None
    qty: Optional[int] = None


async def test_update_exact_sql_and_params(capture):
    from tai42_mcp_dynamic_postgres.gen.templates.update import update_tmpl

    cap = capture(rowcount=1)
    n = await update_tmpl(
        "public.items",
        ["id", "name", "qty"],
        _Upd(name="a", qty=9),
        where=WhereFilter.model_validate({"id": {"eq": 1}}),
    )
    assert rendered(cap) == 'UPDATE "public"."items" SET "name" = %s, "qty" = %s WHERE "id" = %s'
    assert cap["params"] == ["a", 9, 1]
    assert n == 1


async def test_update_empty_payload_raises(capture):
    from tai42_mcp_dynamic_postgres.gen.templates.update import update_tmpl

    capture()
    with pytest.raises(ValueError, match="No fields provided to update"):
        await update_tmpl("public.items", ["id"], _Upd(), where=WhereFilter.model_validate({"id": {"eq": 1}}))


async def test_update_unfiltered_blocked(capture):
    from tai42_mcp_dynamic_postgres.gen.templates.update import update_tmpl

    capture()
    with pytest.raises(ValueError, match="without a WHERE filter"):
        await update_tmpl("public.items", ["id", "name"], _Upd(name="a"))


class _UpdJson(BaseModel):
    meta: Optional[Any] = None


async def test_update_json_column_wrapped(capture):
    from tai42_mcp_dynamic_postgres.gen.templates.update import update_tmpl

    cap = capture(rowcount=1)
    await update_tmpl(
        "public.d",
        ["id", "meta"],
        _UpdJson(meta={"k": 1}),
        where=WhereFilter.model_validate({"id": {"eq": 1}}),
        json_columns=["meta"],
    )
    assert isinstance(cap["params"][0], Json)


async def test_update_explicit_null_sets_null(capture):
    from tai42_mcp_dynamic_postgres.gen.templates.update import update_tmpl

    cap = capture(rowcount=1)
    # name explicitly sent as None -> SET name = NULL (not dropped from the update).
    n = await update_tmpl(
        "public.items",
        ["id", "name", "qty"],
        _Upd(name=None),
        where=WhereFilter.model_validate({"id": {"eq": 1}}),
    )
    assert rendered(cap) == 'UPDATE "public"."items" SET "name" = %s WHERE "id" = %s'
    assert cap["params"] == [None, 1]
    assert n == 1


async def test_update_omitted_field_not_updated(capture):
    from tai42_mcp_dynamic_postgres.gen.templates.update import update_tmpl

    cap = capture(rowcount=1)
    # qty omitted -> only name is in the SET list; qty is left untouched.
    await update_tmpl(
        "public.items",
        ["id", "name", "qty"],
        _Upd(name="a"),
        where=WhereFilter.model_validate({"id": {"eq": 1}}),
    )
    assert rendered(cap) == 'UPDATE "public"."items" SET "name" = %s WHERE "id" = %s'
    assert cap["params"] == ["a", 1]


class _Rogue:
    model_fields_set: ClassVar[set] = {'evil"; DROP'}

    def model_dump(self, *args, **kwargs):
        return {'evil"; DROP': 1}


async def test_update_rogue_field_key_is_quoted(capture):
    from tai42_mcp_dynamic_postgres.gen.templates.update import update_tmpl

    cap = capture(rowcount=1)
    await update_tmpl("public.t", ["c"], _Rogue(), where=WhereFilter.model_validate({"c": {"eq": 1}}))
    assert '"evil""; DROP" = %s' in rendered(cap)


# --- delete -----------------------------------------------------------------


async def test_delete_exact_sql_and_params(capture):
    from tai42_mcp_dynamic_postgres.gen.templates.delete import delete_tmpl

    cap = capture(rowcount=2)
    n = await delete_tmpl("public.items", ["id"], where=WhereFilter.model_validate({"id": {"eq": 1}}))
    assert rendered(cap) == 'DELETE FROM "public"."items" WHERE "id" = %s'
    assert cap["params"] == [1]
    assert n == 2


async def test_delete_unfiltered_blocked(capture):
    from tai42_mcp_dynamic_postgres.gen.templates.delete import delete_tmpl

    capture()
    with pytest.raises(ValueError, match="without a WHERE filter"):
        await delete_tmpl("public.items", ["id"])


async def test_delete_supplied_empty_filter_raises(capture):
    from tai42_mcp_dynamic_postgres.gen.templates.delete import delete_tmpl

    capture()
    with pytest.raises(ValueError, match="empty"):
        await delete_tmpl("public.items", ["id"], where=WhereFilter.model_validate({}))


# --- select -----------------------------------------------------------------


async def test_select_exact_sql_with_offset_and_nulls(capture):
    from tai42_mcp_dynamic_postgres.gen.templates.select import select_tmpl

    cap = capture(rows=[{"id": 1}])
    rows = await select_tmpl(
        "public.items",
        ["id", "name"],
        where=WhereFilter.model_validate({"id": {"eq": 1}}),
        order_by=[OrderByItem(field="name", direction="DESC", nulls="LAST")],
        limit=10,
        offset=5,
    )
    assert rendered(cap) == (
        'SELECT "id", "name" FROM "public"."items" WHERE "id" = %s ORDER BY "name" DESC NULLS LAST LIMIT %s OFFSET %s'
    )
    assert cap["params"] == [1, 10, 5]
    assert rows == [{"id": 1}]


async def test_select_maps_rows_to_model(capture):
    from tai42_mcp_dynamic_postgres.gen.templates.select import select_tmpl

    class Row(BaseModel):
        id: int
        name: Optional[str] = None

    cap = capture(rows=[{"id": 1, "name": "a"}])
    rows = await select_tmpl("public.items", ["id", "name"], model=Row)
    assert rows == [Row(id=1, name="a")]
    assert rendered(cap) == 'SELECT "id", "name" FROM "public"."items"'


async def test_select_projects_only_exposed_columns(capture):
    from tai42_mcp_dynamic_postgres.gen.templates.select import select_tmpl

    cap = capture(rows=[{"id": 1}])
    # Full allowlist keeps "secret" filterable, but the projection omits it, so
    # the ignored column is never fetched from the database.
    await select_tmpl(
        "public.items",
        ["id", "name", "secret"],
        where=WhereFilter.model_validate({"secret": {"eq": "x"}}),
        select_columns=["id", "name"],
    )
    assert rendered(cap) == 'SELECT "id", "name" FROM "public"."items" WHERE "secret" = %s'
    assert '"secret"' in rendered(cap)  # only in the WHERE clause, not the projection
    assert 'SELECT "id", "name" FROM' in rendered(cap)


async def test_select_empty_projection_raises(capture):
    from tai42_mcp_dynamic_postgres.gen.templates.select import select_tmpl

    capture()
    with pytest.raises(ValueError, match="no columns to project"):
        await select_tmpl("public.items", ["id"], select_columns=[])


async def test_select_supplied_empty_filter_raises(capture):
    from tai42_mcp_dynamic_postgres.gen.templates.select import select_tmpl

    capture()
    with pytest.raises(ValueError, match="empty"):
        await select_tmpl("public.items", ["id"], where=WhereFilter.model_validate({}))


# --- select_joined ----------------------------------------------------------


async def test_select_joined_exact_sql_from_identifier_parts(capture):
    from tai42_mcp_dynamic_postgres.gen.templates.select_joined import select_joined_tmpl

    cap = capture(rows=[{"public_users_id": 1}])
    select_items = [
        (["public", "users", "id"], "public_users_id"),
        (["public", "orgs", "title"], "public_orgs_title"),
    ]
    from_parts = ["public", "users"]
    joins = [(["public", "orgs"], [(["public", "users", "org_id"], ["public", "orgs", "id"])])]
    column_map = {"public_users_id": "public.users.id", "public_orgs_title": "public.orgs.title"}

    await select_joined_tmpl(
        select_items,
        from_parts,
        joins,
        column_map,
        where=WhereFilter.model_validate({"public_users_id": {"eq": 1}}),
    )
    assert rendered(cap) == (
        'SELECT "public"."users"."id" AS "public_users_id", "public"."orgs"."title" AS "public_orgs_title" '
        'FROM "public"."users" LEFT JOIN "public"."orgs" ON "public"."users"."org_id" = "public"."orgs"."id" '
        'WHERE "public"."users"."id" = %s'
    )
    assert cap["params"] == [1]


async def test_select_joined_empty_projection_raises(capture):
    from tai42_mcp_dynamic_postgres.gen.templates.select_joined import select_joined_tmpl

    capture()
    with pytest.raises(ValueError, match="no columns to project"):
        await select_joined_tmpl([], ["public", "users"], [], {})
