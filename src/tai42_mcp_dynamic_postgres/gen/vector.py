"""pgvector distance operators and text-literal rendering."""

from typing import Dict, List

from psycopg import sql

# pgvector distance operators by metric name.
KNN_OPS: Dict[str, sql.SQL] = {
    "l2": sql.SQL("<->"),
    "inner_product": sql.SQL("<#>"),
    "cosine": sql.SQL("<=>"),
}


def vector_literal(query: List[float]) -> str:
    """Render a float vector as a pgvector text literal, e.g. ``[1.0,2.0]``."""
    return "[" + ",".join(map(str, query)) + "]"
