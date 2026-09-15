"""Pydantic models describing the ORDER BY payload accepted by generated select tools."""

from typing import List, Literal, Optional

from pydantic import BaseModel


class KnnOrder(BaseModel):
    """Order by vector distance: query vector, distance metric, and direction."""

    model_config = {"extra": "forbid"}

    query: List[float]
    distance: Literal["l2", "inner_product", "cosine"] = "l2"
    direction: Literal["ASC", "DESC"] = "ASC"


class OrderByItem(BaseModel):
    """One sort key: a field (or KNN distance) with direction and NULLS placement."""

    model_config = {"extra": "forbid"}

    field: str
    direction: Literal["ASC", "DESC"] = "ASC"
    # NULLS ordering. When unset, PostgreSQL's default applies (NULLS LAST for
    # ASC, NULLS FIRST for DESC).
    nulls: Optional[Literal["FIRST", "LAST"]] = None
    knn: Optional[KnnOrder] = None
