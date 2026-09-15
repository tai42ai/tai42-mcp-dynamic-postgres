"""Pydantic models describing the WHERE-filter payload accepted by generated tools."""

from typing import Any, Dict, List, Literal, Optional, Union, cast

from pydantic import BaseModel, Field, RootModel, model_validator


class KnnOp(BaseModel):
    """Vector nearest-neighbour operator: query vector, distance metric, and threshold."""

    model_config = {"extra": "forbid"}

    query: List[float]
    distance: Literal["l2", "inner_product", "cosine"] = "l2"
    threshold: float


class FilterOp(BaseModel):
    """The comparison, membership, range, null, and KNN operators for one field."""

    model_config = {"extra": "forbid"}

    eq: Optional[Any] = None  # Equal
    ne: Optional[Any] = None  # Not equal
    gt: Optional[Any] = None  # Greater than
    gte: Optional[Any] = None  # Greater than or equal
    lt: Optional[Any] = None  # Less than
    lte: Optional[Any] = None  # Less than or equal

    # The value is bound verbatim; no wildcards are added. Include % / _ yourself.
    like: Optional[str] = None  # LIKE <value>
    not_like: Optional[str] = None  # NOT LIKE <value>
    ilike: Optional[str] = None  # ILIKE <value> (PostgreSQL only)
    not_ilike: Optional[str] = None  # NOT ILIKE <value> (PostgreSQL only)

    in_: Optional[List[Any]] = Field(default=None, alias="in", min_length=1)  # IN (...)
    not_in: Optional[List[Any]] = Field(default=None, min_length=1)  # NOT IN (...)

    between: Optional[List[Any]] = Field(default=None, min_length=2, max_length=2)  # BETWEEN [x, y]
    is_null: Optional[bool] = None  # IS NULL / IS NOT NULL

    knn: Optional[KnnOp] = None  # Vector KNN operator


class LogicalFilter(BaseModel):
    """AND/OR/NOT combinators over nested :class:`WhereFilter` values."""

    model_config = {"extra": "forbid"}

    AND: Optional[List["WhereFilter"]] = None
    OR: Optional[List["WhereFilter"]] = None
    NOT: Optional["WhereFilter"] = None


class WhereFilter(RootModel[Union[LogicalFilter, Dict[str, FilterOp]]]):
    """A filter that is either a logical combinator or a map of field to operators."""

    model_config = {
        "json_schema_extra": {
            "description": (
                "Logical filters (AND/OR/NOT) or direct field filters "
                "(e.g., { 'field': { 'eq': value, 'in': [values], ... } })"
            )
        }
    }

    @model_validator(mode="before")
    @classmethod
    def reject_mixed_logical_and_field(cls, data: Any) -> Any:
        """Reject an object mixing logical keys with field filters at the same level."""
        # A filter is either logical (AND/OR/NOT) or field filters, never both;
        # mixing would resolve to the logical branch and silently drop the fields.
        if isinstance(data, dict):
            data = cast("dict[str, Any]", data)
            keys = set(data.keys())
            logical = {"AND", "OR", "NOT"} & keys
            if logical and len(keys) > len(logical):
                fields = keys - logical
                raise ValueError(
                    f"Cannot mix logical keys {logical} with field filters {fields} in the "
                    "same object; nest the field filters inside the logical operator instead."
                )
        return data

    @model_validator(mode="after")
    def check_reserved_keys(self):
        """Reject direct field filters that use the reserved AND/OR/NOT names as fields."""
        if isinstance(self.root, dict):
            reserved = {"AND", "OR", "NOT"}
            intersecting = reserved.intersection(self.root.keys())
            if intersecting:
                raise ValueError(f"Cannot use reserved keys {intersecting} as field names in direct filters")
        return self


LogicalFilter.model_rebuild()
