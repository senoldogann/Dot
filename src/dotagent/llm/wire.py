"""Small typed accessors for provider JSON and helpers shared by the wire-format codecs."""

from collections.abc import Sequence

from pydantic import JsonValue

from dotagent.core.errors import JsonParseError, ProviderProtocolError
from dotagent.core.jsonutil import parse_json_value
from dotagent.core.types import JsonObject
from dotagent.llm.types import ImageData, Message, ToolSpec, invalid_arguments, user_message


def get_str(obj: JsonObject, key: str) -> str:
    """The string at ``key``; empty when the key is absent or holds something else."""
    value: JsonValue = obj.get(key)
    return value if isinstance(value, str) else ""


def get_scalar_text(obj: JsonObject, key: str) -> str:
    """Strings and integers rendered as text (error codes are either); empty otherwise."""
    value: JsonValue = obj.get(key)
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    return ""


def get_int(obj: JsonObject, key: str) -> int:
    value: JsonValue = obj.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def get_float(obj: JsonObject, key: str) -> float:
    value: JsonValue = obj.get(key)
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0


def get_obj(obj: JsonObject, key: str) -> JsonObject:
    value: JsonValue = obj.get(key)
    return value if isinstance(value, dict) else {}


def get_list(obj: JsonObject, key: str) -> list[JsonValue]:
    value: JsonValue = obj.get(key)
    return value if isinstance(value, list) else []


def get_objects(obj: JsonObject, key: str) -> list[JsonObject]:
    """The JSON objects inside the list at ``key`` (other entries are skipped)."""
    return [entry for entry in get_list(obj, key) if isinstance(entry, dict)]


def tool_call_id_of(message: Message) -> str:
    """The id of the call a tool message answers; a tool message without one cannot be sent."""
    if message.tool_call_id is None:
        raise ProviderProtocolError("tool message has no call id", {"tool_name": message.tool_name or ""})
    return message.tool_call_id


def data_url(image: ImageData) -> str:
    return f"data:{image.media_type};base64,{image.data_b64}"


def non_blank(text: str, placeholder: str) -> str:
    """``text``, or ``placeholder`` when it is empty (several providers reject empty content)."""
    return text if text.strip() else placeholder


def image_note(tool_names: list[str]) -> str:
    """The line that introduces screenshots re-attached to a user turn because a tool result cannot hold them."""
    return f"[Images returned by tool call(s): {', '.join(sorted(set(tool_names)))}]"


def parse_tool_arguments(raw: str) -> JsonObject:
    """Decode the JSON text of a tool call. Text that is not a JSON object becomes stand-in arguments.

    The model is told about its malformed JSON by the executor instead of the whole run failing.
    """
    if not raw.strip():
        return {}
    try:
        parsed: JsonValue = parse_json_value(raw, "tool call arguments")
    except JsonParseError:
        return invalid_arguments(raw)
    return parsed if isinstance(parsed, dict) else invalid_arguments(raw)


def split_instructions(messages: Sequence[Message]) -> tuple[str, list[Message]]:
    """The leading system messages joined into one instruction text, and the rest of the conversation."""
    leading: int = 0
    while leading < len(messages) and messages[leading].role == "system":
        leading += 1
    text: str = "\n\n".join(message.content for message in messages[:leading] if message.content.strip())
    return text, list(messages[leading:])


def function_tools(tools: Sequence[ToolSpec]) -> list[JsonObject]:
    """Tools in the nested ``{"type": "function", "function": {...}}`` shape of Chat Completions and Ollama."""
    return [
        {
            "type": "function",
            "function": {"name": tool.name, "description": tool.description, "parameters": tool.parameters},
        }
        for tool in tools
    ]


def hoist_tool_images(messages: Sequence[Message]) -> list[Message]:
    """Move screenshots out of tool results into one user message after each run of tool results.

    Chat-style APIs accept images only on user turns, so a tool result keeps its text and its pictures follow it.
    """
    hoisted: list[Message] = []
    names: list[str] = []
    images: list[ImageData] = []

    def flush() -> None:
        if images:
            hoisted.append(user_message(image_note(names), tuple(images)))
            names.clear()
            images.clear()

    for message in messages:
        if message.role != "tool":
            flush()
            hoisted.append(message)
            continue
        if message.images:
            names.append(message.tool_name or "tool")
            images.extend(message.images)
            hoisted.append(message.model_copy(update={"images": ()}))
        else:
            hoisted.append(message)
    flush()
    return hoisted
