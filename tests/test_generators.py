import pytest

from schema_builders import col, schema, table
from tai42_mcp_dynamic_postgres.gen.builders.delete_gen import DeleteGen
from tai42_mcp_dynamic_postgres.gen.builders.insert_gen import InsertGen
from tai42_mcp_dynamic_postgres.gen.builders.select_gen import SelectGen
from tai42_mcp_dynamic_postgres.gen.builders.select_joined_gen import SelectJoinedGen
from tai42_mcp_dynamic_postgres.gen.builders.update_gen import UpdateGen

USERS = table(
    "public.users",
    [
        col("id", "int", has_default=True),
        col("name", "Optional[str]"),
        col("org_id", "Optional[int]"),
    ],
    pk=["id"],
)
ORGS = table(
    "public.orgs",
    [col("id", "int", has_default=True), col("title", "Optional[str]")],
    pk=["id"],
)
TABLES = schema(USERS, ORGS)
FKS = [("public.users", ["org_id"], "public.orgs", ["id"])]


def generated(gen, tables=TABLES, fks=FKS):
    code = "".join(gen.generate_tools(tables, fks))
    compile(code, "<gen>", "exec")  # SyntaxError if invalid
    return code


def test_all_generators_compile():
    for gen in (
        SelectGen(),
        InsertGen(),
        UpdateGen(),
        DeleteGen(),
        SelectJoinedGen([["public.users", "public.orgs"]]),
    ):
        generated(gen)


@pytest.mark.parametrize(
    "gen",
    [
        SelectGen(),
        InsertGen(),
        UpdateGen(),
        DeleteGen(),
        SelectJoinedGen([["public.users", "public.orgs"]]),
    ],
)
def test_generated_tools_carry_postgres_tag(gen):
    assert '@mcp_app.tool(tags={"postgres"})' in generated(gen)


def test_select_passes_full_column_allowlist():
    code = generated(SelectGen(ignore_columns=["id"]))
    # id excluded from returned model but still allowed for WHERE/ORDER BY.
    assert "['id', 'name', 'org_id']" in code


def test_select_projection_excludes_ignored_columns():
    code = generated(SelectGen(ignore_columns=["id"]))
    # The SQL projection lists only exposed columns, so ignored ones are never
    # fetched from the database (not merely dropped from the returned model).
    assert "select_columns=['name', 'org_id']" in code


def test_select_all_columns_ignored_skips_tool():
    # Every column ignored -> empty projection and empty Row model; the tool
    # would raise on every call, so skip it like insert/update do.
    notes = table("public.notes", [col("id", "int", has_default=True), col("body", "Optional[str]")], pk=["id"])
    gen = SelectGen(ignore_columns=["id", "body"])
    assert gen.generate_tool(notes) is None
    code = generated(gen, tables=schema(notes), fks=[])
    assert "async def select_" not in code


def test_select_joined_all_columns_ignored_skips_tool():
    # Every projected column ignored -> empty SELECT list and empty Row model;
    # skip the dead joined tool like the single-table select does.
    tables = schema(
        table("public.users", [col("id", "int", has_default=True), col("org_id", "Optional[int]")], pk=["id"]),
        table("public.orgs", [col("id", "int", has_default=True)], pk=["id"]),
    )
    fks = [("public.users", ["org_id"], "public.orgs", ["id"])]
    gen = SelectJoinedGen([["public.users", "public.orgs"]], ignore_columns=["id", "org_id"])
    assert gen.generate_join_tool(["public.users", "public.orgs"], tables, fks) is None
    code = generated(gen, tables=tables, fks=fks)
    assert "async def select_joined_" not in code


def test_insert_threads_provided_fields():
    code = generated(InsertGen())
    # model_fields_set is threaded so omitted -> DEFAULT and explicit null -> NULL.
    assert "provided_fields = [row.model_fields_set for row in rows]" in code
    assert "provided_fields=provided_fields" in code


def test_select_tool_exposes_offset():
    assert "offset: Optional[int] = None" in generated(SelectGen())


def test_delete_default_blocks_unfiltered():
    assert "allow_unfiltered=False" in generated(DeleteGen())


def test_delete_allow_unfiltered_flag():
    assert "allow_unfiltered=True" in generated(DeleteGen(allow_unfiltered=True))


def test_update_default_blocks_unfiltered():
    assert "allow_unfiltered=False" in generated(UpdateGen())


def test_update_allow_unfiltered_flag():
    assert "allow_unfiltered=True" in generated(UpdateGen(allow_unfiltered=True))


def test_insert_ignore_columns_excluded_from_model():
    code = generated(InsertGen(ignore_columns=["id"]))
    model_section = code.split("class ", 1)[1]
    assert "    id:" not in model_section


def test_insert_default_column_is_optional_in_model():
    # A column with a DB default is omittable on insert.
    tbl = schema(
        table(
            "public.t",
            [col("id", "int", has_default=True), col("created_at", "datetime.datetime", has_default=True)],
            pk=["id"],
        )
    )
    code = generated(InsertGen(ignore_columns=[]), tables=tbl, fks=[])
    assert "created_at: Optional[datetime.datetime] = None" in code


def test_single_column_insert_builds_a_tuple():
    tbl = schema(table("public.notes", [col("id", "int", has_default=True), col("body", "Optional[str]")], pk=["id"]))
    code = generated(InsertGen(ignore_columns=["id"]), tables=tbl, fks=[])
    assert "(row.body,)" in code


def test_insert_all_columns_ignored_skips_tool():
    # An all-columns-ignored table must not emit an invalid empty values tuple.
    tbl = schema(table("public.notes", [col("id", "int", has_default=True), col("body", "Optional[str]")], pk=["id"]))
    code = generated(InsertGen(ignore_columns=["id", "body"]), tables=tbl, fks=[])
    assert "async def insert_" not in code


def test_insert_pk_missing_from_columns_raises():
    # A PK column absent from the introspected columns signals inconsistent
    # introspection: fail loudly instead of widening the return type to Any.
    tbl = schema(table("public.t", [col("name", "Optional[str]")], pk=["ghost"]))
    with pytest.raises(ValueError, match="Primary-key column 'ghost'"):
        generated(InsertGen(ignore_columns=[]), tables=tbl, fks=[])


def test_insert_returns_actual_pk_not_id():
    tbl = schema(table("public.acct", [col("code", "str"), col("name", "Optional[str]")], pk=["code"]))
    code = generated(InsertGen(ignore_columns=[]), tables=tbl, fks=[])
    assert "pk_columns=['code']" in code
    assert "-> List[str]:" in code


def test_insert_composite_pk_returns_tuples():
    tbl = schema(table("public.link", [col("a", "int"), col("b", "int")], pk=["a", "b"]))
    code = generated(InsertGen(ignore_columns=[]), tables=tbl, fks=[])
    assert "pk_columns=['a', 'b']" in code
    assert "-> List[List[Any]]:" in code


def test_insert_no_pk_returns_int():
    tbl = schema(table("public.log", [col("msg", "Optional[str]")], pk=[]))
    code = generated(InsertGen(ignore_columns=[]), tables=tbl, fks=[])
    assert "pk_columns=[]" in code
    assert "-> int:" in code


def test_insert_marks_json_columns():
    tbl = schema(
        table("public.doc", [col("id", "int", has_default=True), col("meta", "Optional[Any]", is_json=True)], pk=["id"])
    )
    code = generated(InsertGen(ignore_columns=["id"]), tables=tbl, fks=[])
    assert "json_columns=['meta']" in code


def test_update_all_columns_ignored_skips_tool():
    # Every updatable column ignored -> the tool could never set a field and
    # would always raise at call time; skip it like insert does.
    tbl = schema(table("public.notes", [col("id", "int", has_default=True), col("body", "Optional[str]")], pk=["id"]))
    code = generated(UpdateGen(ignore_columns=["id", "body"]), tables=tbl, fks=[])
    assert "async def update_" not in code


def test_update_marks_json_columns():
    tbl = schema(
        table("public.doc", [col("id", "int", has_default=True), col("meta", "Optional[Any]", is_json=True)], pk=["id"])
    )
    code = generated(UpdateGen(ignore_columns=["id"]), tables=tbl, fks=[])
    assert "json_columns=['meta']" in code


def test_views_get_no_write_tools_but_do_get_select():
    view = table("public.v_users", [col("id", "int"), col("name", "Optional[str]")], kind="v")
    tbl = schema(view)
    assert "async def insert_" not in generated(InsertGen(), tables=tbl, fks=[])
    assert "async def update_" not in generated(UpdateGen(), tables=tbl, fks=[])
    assert "async def delete_" not in generated(DeleteGen(), tables=tbl, fks=[])
    assert "async def select_public_v_users" in generated(SelectGen(), tables=tbl, fks=[])


def test_partitioned_table_gets_write_tools():
    part = table("public.events", [col("id", "int", has_default=True), col("k", "Optional[str]")], pk=["id"], kind="p")
    tbl = schema(part)
    assert "async def insert_public_events" in generated(InsertGen(), tables=tbl, fks=[])


def test_join_schema_qualified_aliases():
    code = generated(SelectJoinedGen([["public.users", "public.orgs"]]))
    # Aliases are schema-qualified (schema_table_column) to avoid collisions.
    assert "public_users_id" in code
    assert "public_orgs_id" in code


def test_join_embeds_structured_condition_not_raw_sql():
    code = generated(SelectJoinedGen([["public.users", "public.orgs"]]))
    # No raw f-string JOIN SQL; the identifier parts are embedded as data.
    assert "LEFT JOIN public.orgs ON" not in code
    assert "['public', 'users', 'org_id']" in code
    assert "['public', 'orgs', 'id']" in code


def test_composite_foreign_key_join():
    tables = schema(
        table("public.a", [col("k1", "int"), col("k2", "int")]),
        table("public.b", [col("k1", "int"), col("k2", "int")]),
    )
    fks = [("public.a", ["k1", "k2"], "public.b", ["k1", "k2"])]
    code = generated(SelectJoinedGen([["public.a", "public.b"]]), tables=tables, fks=fks)
    # Both composite conditions are present as identifier-part pairs.
    assert "['public', 'a', 'k1']" in code
    assert "['public', 'b', 'k2']" in code


def test_join_reverse_fk_direction():
    # FK declared as orgs -> users still joins when users is listed first.
    fks = [("public.orgs", ["owner_id"], "public.users", ["id"])]
    tables = schema(
        table("public.users", [col("id", "int", has_default=True)], pk=["id"]),
        table("public.orgs", [col("id", "int", has_default=True), col("owner_id", "Optional[int]")], pk=["id"]),
    )
    code = generated(SelectJoinedGen([["public.users", "public.orgs"]]), tables=tables, fks=fks)
    assert "['public', 'orgs', 'owner_id']" in code
    assert "['public', 'users', 'id']" in code


def test_join_three_tables():
    tables = schema(
        table("public.a", [col("id", "int", has_default=True)], pk=["id"]),
        table("public.b", [col("id", "int", has_default=True), col("a_id", "Optional[int]")], pk=["id"]),
        table("public.c", [col("id", "int", has_default=True), col("b_id", "Optional[int]")], pk=["id"]),
    )
    fks = [
        ("public.b", ["a_id"], "public.a", ["id"]),
        ("public.c", ["b_id"], "public.b", ["id"]),
    ]
    generated(SelectJoinedGen([["public.a", "public.b", "public.c"]]), tables=tables, fks=fks)


def test_join_table_not_in_schema_raises():
    with pytest.raises(ValueError, match="not found in schema"):
        SelectJoinedGen([["public.users", "public.ghost"]]).generate_tools(
            schema(USERS), [("public.users", ["org_id"], "public.ghost", ["id"])]
        )


def test_join_alias_collision_raises():
    # The same table listed twice produces duplicate aliases.
    users = table("public.users", [col("id", "int", has_default=True), col("name", "Optional[str]")], pk=["id"])
    tables = schema(users)
    fks = [("public.users", ["id"], "public.users", ["id"])]
    with pytest.raises(ValueError, match="collision"):
        SelectJoinedGen([["public.users", "public.users"]]).generate_tools(tables, fks)


def test_join_requires_two_tables():
    with pytest.raises(ValueError, match="at least two tables"):
        SelectJoinedGen([["public.users"]]).generate_tools(TABLES, FKS)


def test_join_requires_foreign_key():
    tables = schema(table("public.a", [col("id", "int")]), table("public.b", [col("id", "int")]))
    with pytest.raises(ValueError, match="No foreign key relationship"):
        SelectJoinedGen([["public.a", "public.b"]]).generate_tools(tables, [])
