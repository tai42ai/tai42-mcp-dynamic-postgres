"""Introspect a live PostgreSQL schema into table, column, and foreign-key models."""

import keyword
import re
from dataclasses import dataclass
from typing import Dict, List, NamedTuple, Set, Tuple

from tai42_mcp_dynamic_postgres.database.connection import cursor
from tai42_mcp_dynamic_postgres.gen.schema.codegen import is_json_type, sql_type_to_python_type


class ColumnInfo(NamedTuple):
    """One introspected column."""

    name: str
    # Python annotation string, e.g. ``Optional[int]`` or ``datetime.datetime``.
    python_type: str
    # json/jsonb column: its bound value must be wrapped in ``Json(...)``.
    is_json: bool
    # Column has a database default (incl. serial/identity), so it is omittable
    # on insert and the default applies when omitted.
    has_default: bool
    nullable: bool


@dataclass
class TableInfo:
    """One introspected relation (table, partitioned table, view, or matview)."""

    schema: str
    name: str
    # pg_class.relkind: 'r' ordinary, 'p' partitioned, 'v' view, 'm' matview.
    kind: str
    columns: List[ColumnInfo]
    # Primary-key column names in key order; empty when the relation has no PK.
    primary_key: List[str]

    @property
    def qualified(self) -> str:
        """The ``schema.name`` identifier of this relation."""
        return f"{self.schema}.{self.name}"

    @property
    def writable(self) -> bool:
        """Whether this relation accepts DML (ordinary or partitioned tables only)."""
        # Ordinary and partitioned tables accept DML; views/matviews are read-only.
        return self.kind in ("r", "p")


# A foreign key as (table, [columns], ref_table, [ref_columns]); columns are
# ordered to line up positionally, so composite keys join correctly.
ForeignKey = Tuple[str, List[str], str, List[str]]

# Names become Python function/field names and identifiers in generated SQL, so
# they must be valid, non-reserved identifiers. Leading-underscore names are also
# rejected: Pydantic v2 treats them as private attributes, silently dropping the
# column.
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def _require_safe_identifier(kind: str, name: str) -> None:
    # Reject only hard keywords; soft keywords (match, case, type) stay valid
    # identifiers and are common column names.
    if not _SAFE_IDENTIFIER.fullmatch(name) or keyword.iskeyword(name):
        raise ValueError(
            f"Unsupported {kind} name {name!r}: tool generation requires identifiers that are valid, "
            f"non-reserved Python names that start with a letter ([A-Za-z][A-Za-z0-9_]*); a "
            f"leading-underscore name would become a Pydantic private attribute and silently drop the "
            f"column. Rename it, or exclude it from the exposed schema (e.g. via a dedicated database "
            f"role/search_path)."
        )


# User-defined enum type names; enum columns map to str rather than aborting.
_ENUM_QUERY = """
    SELECT t.typname
    FROM pg_type t
        JOIN pg_namespace n ON n.oid = t.typnamespace
    WHERE t.typtype = 'e'
      AND n.nspname NOT IN ('pg_catalog', 'information_schema');
"""

# Ordinary ('r'), partitioned ('p'), view ('v'), and materialized view ('m')
# relations. has_default covers explicit DEFAULTs and identity columns, so those
# columns can be omitted on insert.
_COLUMN_QUERY = """
    SELECT n.nspname AS schema,
           c.relname AS table,
           c.relkind AS kind,
           a.attname AS column,
           pg_catalog.format_type(a.atttypid, a.atttypmod) AS type,
           a.attnotnull AS notnull,
           (a.atthasdef OR a.attidentity <> '') AS has_default
    FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid
    WHERE c.relkind IN ('r', 'p', 'v', 'm')
      AND a.attnum > 0
      AND NOT a.attisdropped
      AND n.nspname NOT IN ('pg_catalog', 'information_schema')
    ORDER BY n.nspname, c.relname, a.attnum;
"""

# unnest(conkey) WITH ORDINALITY preserves primary-key column order for
# composite keys.
_PK_QUERY = """
    SELECT ns.nspname AS schema,
           cl.relname AS table,
           att.attname AS column
    FROM pg_constraint con
        JOIN pg_class cl ON cl.oid = con.conrelid
        JOIN pg_namespace ns ON ns.oid = cl.relnamespace
        JOIN LATERAL unnest(con.conkey) WITH ORDINALITY AS k(attnum, ord) ON true
        JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = k.attnum
    WHERE con.contype = 'p'
      AND ns.nspname NOT IN ('pg_catalog', 'information_schema')
    ORDER BY ns.nspname, cl.relname, k.ord;
"""

# unnest(conkey, confkey) WITH ORDINALITY keeps composite-key columns aligned
# and ordered, so multi-column foreign keys are reconstructed correctly.
_FK_QUERY = """
    SELECT con.conname,
           ns.nspname  AS table_schema,
           cl.relname  AS table_name,
           att.attname AS column_name,
           fns.nspname AS ref_schema,
           fcl.relname AS ref_table,
           fatt.attname AS ref_column
    FROM pg_constraint con
        JOIN pg_class cl ON cl.oid = con.conrelid
        JOIN pg_namespace ns ON ns.oid = cl.relnamespace
        JOIN pg_class fcl ON fcl.oid = con.confrelid
        JOIN pg_namespace fns ON fns.oid = fcl.relnamespace
        JOIN LATERAL unnest(con.conkey, con.confkey) WITH ORDINALITY AS k(att, fatt, ord) ON true
        JOIN pg_attribute att ON att.attrelid = con.conrelid AND att.attnum = k.att
        JOIN pg_attribute fatt ON fatt.attrelid = con.confrelid AND fatt.attnum = k.fatt
    WHERE con.contype = 'f'
      AND ns.nspname NOT IN ('pg_catalog', 'information_schema')
    ORDER BY ns.nspname, cl.relname, con.conname, k.ord;
"""


async def introspect_schema() -> Tuple[Dict[str, TableInfo], List[ForeignKey]]:
    """Read the live schema and return ``(tables, foreign_keys)`` directly.

    ``tables`` maps ``schema.table`` to its :class:`TableInfo` (columns in
    ordinal order, primary key, and relation kind).
    """
    tables: Dict[str, TableInfo] = {}
    # Constraint names are unique only per table, so group by
    # ``(schema, table, conname)`` — not ``conname`` alone — to keep same-named
    # constraints on different tables separate.
    fk_groups: Dict[Tuple[str, str, str], ForeignKey] = {}

    async with cursor() as cur:
        await cur.execute(_ENUM_QUERY)
        enum_types: Set[str] = {row[0] for row in await cur.fetchall()}

        await cur.execute(_COLUMN_QUERY)
        for schema, table, kind, column, col_type, notnull, has_default in await cur.fetchall():
            _require_safe_identifier("schema", schema)
            _require_safe_identifier("table", table)
            _require_safe_identifier("column", column)
            nullable = not notnull
            py_type = sql_type_to_python_type(col_type, nullable=nullable, enum_types=enum_types)
            col = ColumnInfo(
                name=column,
                python_type=py_type,
                is_json=is_json_type(col_type),
                has_default=bool(has_default),
                nullable=nullable,
            )
            key = f"{schema}.{table}"
            if key not in tables:
                tables[key] = TableInfo(schema=schema, name=table, kind=kind, columns=[], primary_key=[])
            tables[key].columns.append(col)

        await cur.execute(_PK_QUERY)
        for schema, table, column in await cur.fetchall():
            key = f"{schema}.{table}"
            if key in tables:
                tables[key].primary_key.append(column)

        await cur.execute(_FK_QUERY)
        for conname, schema, table, column, ref_schema, ref_table, ref_column in await cur.fetchall():
            for kind_name, name in (
                ("schema", schema),
                ("table", table),
                ("column", column),
                ("schema", ref_schema),
                ("table", ref_table),
                ("column", ref_column),
            ):
                _require_safe_identifier(kind_name, name)
            group_key = (schema, table, conname)
            if group_key not in fk_groups:
                fk_groups[group_key] = (f"{schema}.{table}", [], f"{ref_schema}.{ref_table}", [])
            _, cols, _, ref_cols = fk_groups[group_key]
            cols.append(column)
            ref_cols.append(ref_column)

    return tables, list(fk_groups.values())
