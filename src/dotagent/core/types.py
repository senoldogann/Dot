"""Base model and JSON aliases shared by every layer."""

from pydantic import BaseModel, ConfigDict, JsonValue

type JsonObject = dict[str, JsonValue]
type JsonArray = list[JsonValue]


class FrozenModel(BaseModel):
    """Immutable, strict base for all value types."""

    model_config = ConfigDict(frozen=True, extra="forbid")
