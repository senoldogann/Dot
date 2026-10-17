"""OpenCode's event stream as a run on one of Zen's free models reads it (item 283): the events of OpenCode's server
(``GET /event``) that concern the run's session, read into what the run shows. OpenCode sends many more (its plugins,
its catalog, its watchers, other sessions); those read as ``None``. The shapes are those of OpenCode 1.19.0's OpenAPI
document (``GET /doc``); a field missing from an event reads as empty, as the provider codecs read their JSON."""

from typing import Literal

from pydantic import JsonValue

from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.wire import get_int, get_list, get_obj, get_str

type PieceKind = Literal["text", "thinking"]
type ToolStatus = Literal["pending", "running", "completed", "error"]
_TOOL_STATUSES: tuple[ToolStatus, ...] = ("pending", "running", "completed", "error")


class PartSeen(FrozenModel):
    """A part of text or of reasoning as it stands: the run learns from it which kind the part's pieces are."""

    part_id: str
    message_id: str
    kind: PieceKind
    text: str


class Piece(FrozenModel):
    """Text the model added to a part."""

    part_id: str
    message_id: str
    text: str


class ToolSeen(FrozenModel):
    """A call of one of OpenCode's tools as it stands: asked for, at work, done (``output``) or failed (``error``);
    the times are OpenCode's, in milliseconds since 1970."""

    call_id: str
    message_id: str
    tool: str
    status: ToolStatus
    input: JsonObject
    output: str
    error: str
    title: str
    started_ms: int | None
    ended_ms: int | None


class StepStarted(FrozenModel):
    message_id: str


class StepEnded(FrozenModel):
    """A step of the model ended: what it read (``input_tokens``, of which ``cached_tokens`` from the cache) and
    wrote."""

    message_id: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    reason: str


class MessageSeen(FrozenModel):
    """A message of the session as it stands; ``error`` is set on an assistant message that ended with one, and
    ``error_detail`` is that error as OpenCode sends it."""

    message_id: str
    role: Literal["user", "assistant"]
    error: str | None
    error_detail: JsonObject


class PermissionAsked(FrozenModel):
    """OpenCode asks whether one of its tools may act: ``permission`` names the kind (``bash``, ``edit``,
    ``webfetch``, …), ``patterns`` what it would act on, ``call_id`` the tool call that asks."""

    request_id: str
    permission: str
    patterns: tuple[str, ...]
    metadata: JsonObject
    call_id: str | None


class SessionIdle(FrozenModel):
    """The session has nothing left to do: the agent answered, or stopped."""

    session_id: str


class SessionFailed(FrozenModel):
    message: str
    detail: JsonObject


type SessionEvent = (
    PartSeen | Piece | ToolSeen | StepStarted | StepEnded | MessageSeen | PermissionAsked | SessionIdle | SessionFailed
)


def _ms(obj: JsonObject, key: str) -> int | None:
    value: JsonValue = obj.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _tool(part: JsonObject, message_id: str) -> ToolSeen | None:
    state: JsonObject = get_obj(part, "state")
    status: str = get_str(state, "status")
    call_id: str = get_str(part, "callID")
    if call_id == "" or status not in _TOOL_STATUSES:
        return None
    times: JsonObject = get_obj(state, "time")
    return ToolSeen(
        call_id=call_id,
        message_id=message_id,
        tool=get_str(part, "tool"),
        status=next(known for known in _TOOL_STATUSES if known == status),
        input=get_obj(state, "input"),
        output=get_str(state, "output"),
        error=get_str(state, "error"),
        title=get_str(state, "title"),
        started_ms=_ms(times, "start"),
        ended_ms=_ms(times, "end"),
    )


def _part(part: JsonObject) -> SessionEvent | None:
    part_id: str = get_str(part, "id")
    message_id: str = get_str(part, "messageID")
    if part_id == "" or message_id == "":
        return None
    match get_str(part, "type"):
        case "text":
            return PartSeen(part_id=part_id, message_id=message_id, kind="text", text=get_str(part, "text"))
        case "reasoning":
            return PartSeen(part_id=part_id, message_id=message_id, kind="thinking", text=get_str(part, "text"))
        case "tool":
            return _tool(part, message_id)
        case "step-start":
            return StepStarted(message_id=message_id)
        case "step-finish":
            tokens: JsonObject = get_obj(part, "tokens")
            cached: int = get_int(get_obj(tokens, "cache"), "read")
            return StepEnded(
                message_id=message_id,
                input_tokens=get_int(tokens, "input") + cached,
                output_tokens=get_int(tokens, "output") + get_int(tokens, "reasoning"),
                cached_tokens=cached,
                reason=get_str(part, "reason"),
            )
        case _:
            return None


def _error_text(error: JsonObject) -> str:
    """What an error of OpenCode says: its data's message, else its name."""
    message: str = get_str(get_obj(error, "data"), "message")
    return message if message != "" else get_str(error, "name")


def _message(info: JsonObject) -> MessageSeen | None:
    message_id: str = get_str(info, "id")
    role: str = get_str(info, "role")
    if message_id == "" or role not in ("user", "assistant"):
        return None
    error: JsonObject = get_obj(info, "error")
    return MessageSeen(
        message_id=message_id,
        role="user" if role == "user" else "assistant",
        error=_error_text(error) if error else None,
        error_detail=error,
    )


def _session_of(kind: str, properties: JsonObject) -> str:
    """The session an event concerns; empty for an event of no session."""
    if kind == "message.part.updated":
        return get_str(get_obj(properties, "part"), "sessionID")
    if kind == "message.updated":
        return get_str(get_obj(properties, "info"), "sessionID")
    return get_str(properties, "sessionID")


def read_event(raw: JsonObject, session_id: str) -> SessionEvent | None:
    """The event ``raw`` of OpenCode's stream as the run of the session ``session_id`` reads it; ``None`` for an event
    of another session or of a kind the run does not show."""
    kind: str = get_str(raw, "type")
    properties: JsonObject = get_obj(raw, "properties")
    if _session_of(kind, properties) != session_id:
        return None
    match kind:
        case "message.part.updated":
            return _part(get_obj(properties, "part"))
        case "message.part.delta":
            part_id: str = get_str(properties, "partID")
            if part_id == "" or get_str(properties, "field") != "text":
                return None
            return Piece(
                part_id=part_id, message_id=get_str(properties, "messageID"), text=get_str(properties, "delta")
            )
        case "message.updated":
            return _message(get_obj(properties, "info"))
        case "permission.asked":
            request_id: str = get_str(properties, "id")
            if request_id == "":
                return None
            call_id: str = get_str(get_obj(properties, "tool"), "callID")
            return PermissionAsked(
                request_id=request_id,
                permission=get_str(properties, "permission"),
                patterns=tuple(str(item) for item in get_list(properties, "patterns") if isinstance(item, str)),
                metadata=get_obj(properties, "metadata"),
                call_id=call_id if call_id != "" else None,
            )
        case "session.idle":
            return SessionIdle(session_id=session_id)
        case "session.error":
            error: JsonObject = get_obj(properties, "error")
            return SessionFailed(message=_error_text(error) or "OpenCode's session failed", detail=error)
        case _:
            return None
