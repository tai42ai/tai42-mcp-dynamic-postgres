import warnings
from typing import Any, List

from psycopg import AsyncConnection
from psycopg.abc import Buffer
from psycopg.adapt import Loader
from psycopg.types import TypeInfo
from psycopg.types.json import JsonDumper


class VectorLoader(Loader):
    def load(self, data: Buffer) -> List[float]:
        s = bytes(data).decode("utf-8")
        if s.startswith("[") and s.endswith("]"):
            values = s[1:-1].split(",")
            return [float(x.strip()) for x in values if x.strip()]
        else:
            raise ValueError(f"Invalid vector format: {s}")


async def register_vector_as_list(conn: AsyncConnection[Any]) -> None:
    tinfo = await TypeInfo.fetch(conn, "vector")
    if not tinfo:
        warnings.warn(
            "Postgres type 'vector' not found. Did you enable the pgvector extension?",
            RuntimeWarning,
            stacklevel=2,
        )
    else:
        conn.adapters.register_loader(tinfo.oid, VectorLoader)


def register_json_dumpers(conn: AsyncConnection[Any]) -> None:
    # Safety net so a bare ``dict`` binds as JSON. Generated insert/update tools
    # already wrap json/jsonb values in ``Json(...)`` by column type, so array
    # columns keep their native list adapter.
    conn.adapters.register_dumper(dict, JsonDumper)


async def register_types_loaders(conn: AsyncConnection[Any]) -> None:
    # Temporal and uuid types keep psycopg's native loaders, matching the native
    # annotations the code generator emits for those columns.
    await register_vector_as_list(conn)
    register_json_dumpers(conn)
