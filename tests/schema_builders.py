"""Helpers for building introspection structures in unit tests."""

from typing import Dict, Iterable, Sequence

from tai42_mcp_dynamic_postgres.gen.schema.introspect import ColumnInfo, TableInfo


def col(
    name: str,
    python_type: str,
    *,
    is_json: bool = False,
    has_default: bool = False,
    nullable: bool | None = None,
) -> ColumnInfo:
    if nullable is None:
        nullable = python_type.startswith("Optional[")
    return ColumnInfo(
        name=name,
        python_type=python_type,
        is_json=is_json,
        has_default=has_default,
        nullable=nullable,
    )


def table(
    qualified: str,
    columns: Iterable[ColumnInfo],
    *,
    pk: Sequence[str] = (),
    kind: str = "r",
) -> TableInfo:
    schema, name = qualified.split(".")
    return TableInfo(schema=schema, name=name, kind=kind, columns=list(columns), primary_key=list(pk))


def schema(*tables: TableInfo) -> Dict[str, TableInfo]:
    return {t.qualified: t for t in tables}
