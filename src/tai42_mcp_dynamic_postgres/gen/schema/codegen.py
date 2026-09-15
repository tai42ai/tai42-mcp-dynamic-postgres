"""Map PostgreSQL types to Python annotations and build Pydantic row models."""

from typing import AbstractSet, FrozenSet, List, Tuple

# PostgreSQL scalar type -> Python annotation. Arrays derive ``T[]`` -> ``List[T]``;
# modifiers like ``numeric(10,2)`` are stripped. Temporal/uuid/numeric/json map to
# native objects, not ``str`` (psycopg returns them native).
SQL_TO_PYTHON = {
    # Integer types
    "serial": "int",
    "bigserial": "int",
    "smallserial": "int",
    "smallint": "int",
    "integer": "int",
    "int": "int",
    "bigint": "int",
    # Floating point
    "real": "float",
    "double precision": "float",
    # Exact numeric -> Decimal (psycopg returns decimal.Decimal)
    "numeric": "Decimal",
    "decimal": "Decimal",
    # Boolean
    "boolean": "bool",
    "bool": "bool",
    # Character types
    "text": "str",
    "varchar": "str",
    "character varying": "str",
    "char": "str",
    "character": "str",
    # UUID -> uuid.UUID (psycopg returns uuid.UUID)
    "uuid": "uuid.UUID",
    # Temporal types -> native datetime objects
    "date": "datetime.date",
    "timestamp": "datetime.datetime",
    "timestamp without time zone": "datetime.datetime",
    "timestamp with time zone": "datetime.datetime",
    "timestamptz": "datetime.datetime",
    "time": "datetime.time",
    "time without time zone": "datetime.time",
    "time with time zone": "datetime.time",
    # JSON types -> Any (a scalar json value such as 42, "x", or true must validate)
    "json": "Any",
    "jsonb": "Any",
    # Other scalars
    "bytea": "bytes",
    "inet": "str",
    "cidr": "str",
    "macaddr": "str",
    "interval": "str",
    # Vector (pgvector)  # noqa: ERA001 - section label for the pgvector entry, not commented-out code
    "vector": "List[float]",
}

# json/jsonb column values must be adapted with ``Json(...)`` when bound; every
# other type (including arrays) is passed natively.
_JSON_TYPES: FrozenSet[str] = frozenset({"json", "jsonb"})


def _base_type(sql_type: str) -> str:
    """Strip a type modifier such as ``numeric(10,2)`` or ``vector(768)``."""
    return sql_type.split("(", 1)[0].strip()


def _scalar_python_type(sql_type: str, enum_types: AbstractSet[str]) -> str:
    base = _base_type(sql_type)
    # A user-defined enum maps to str; its type name is not in SQL_TO_PYTHON.
    if base in enum_types or base.split(".")[-1] in enum_types:
        return "str"
    if sql_type in SQL_TO_PYTHON:
        return SQL_TO_PYTHON[sql_type]
    if base in SQL_TO_PYTHON:
        return SQL_TO_PYTHON[base]
    raise ValueError(
        f"Unsupported PostgreSQL type {sql_type!r}: no Python mapping. "
        f"Add it to SQL_TO_PYTHON, or exclude the column from the exposed schema."
    )


def sql_type_to_python_type(sql_type: str, nullable: bool, enum_types: AbstractSet[str] = frozenset()) -> str:
    """Map a ``pg_catalog.format_type`` string to a Python annotation.

    Unknown types raise rather than silently widening to ``Any``. ``enum_types``
    is the set of user-defined enum type names; enum columns map to ``str``.
    """
    sql_type = sql_type.lower().strip()
    if sql_type.endswith("[]"):
        py_type = f"List[{_scalar_python_type(sql_type[:-2].strip(), enum_types)}]"
    else:
        py_type = _scalar_python_type(sql_type, enum_types)
    return f"Optional[{py_type}]" if nullable else py_type


def is_json_type(sql_type: str) -> bool:
    """True if a scalar json/jsonb column (whose value must be ``Json``-wrapped)."""
    sql_type = sql_type.lower().strip()
    if sql_type.endswith("[]"):
        return False
    return _base_type(sql_type) in _JSON_TYPES


def sql_columns_to_pydantic_model(prefix: str, table: str, columns: List[Tuple[str, str]]) -> Tuple[str, str]:
    """Return the model class name and its generated source for the given columns.

    Optional-typed columns default to ``None``; an empty column list yields a
    body of ``pass``.
    """
    name = f"{prefix}_{table}_row"
    model_name = "".join(word.capitalize() for word in name.replace(".", "_").split("_"))

    def format_field(col: str, typ: str) -> str:
        default = " = None" if typ.startswith("Optional[") else ""
        return f"    {col}: {typ}{default}"

    body = "\n".join(format_field(col, typ) for col, typ in columns) if columns else "    pass"
    model_code = f"\nclass {model_name}(BaseModel):\n{body}\n"
    return model_name, model_code
