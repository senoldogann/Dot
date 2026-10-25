"""What the earlier turns of a chat used, read and remembered (item 650), for the context of the Run tab: the panel
keeps the whole chat's from its start, not only the open run's, which a new message used to start again from nothing.
Each earlier turn's tool calls that succeeded, oldest first, each with the arguments the panel reads (a search's words,
a page's address, a skill's name, a note's first words) and, for a change of the memory, what its result says. A string
of the arguments is cut, as the panel shows a line of it at most; a delegated run's calls are its own page's."""

from pydantic import JsonValue

from dotagent.core.types import FrozenModel, JsonObject
from dotagent.store.db import Store
from dotagent.store.models import EventRecord, RunRecord

# The turns of a chat read at most, as the Changes do (server.changes.CHAT_RUNS).
CONTEXT_TURNS: int = 200
# A string of a call's arguments is cut at this many characters: the panel shows one line of it. An address is not
# (``_ADDRESS_KEYS``), as the panel's row opens it: one cut would open another page; it is held to a URL's own bound.
_STRING_CHARS: int = 300
_ADDRESS_CHARS: int = 8192
_ADDRESS_KEYS: frozenset[str] = frozenset({"url", "urls", "start_url"})
# The kind of a tool result that changed the memory, whose text and summary say how (timeline.js `memoryChange`).
_MEMORY_WRITE: str = "memory_write"


class ContextStep(FrozenModel):
    """A tool call of an earlier turn that succeeded: its tool, its arguments, and for a change of the memory the
    result's kind, text and summary (empty otherwise)."""

    tool: str
    args: JsonObject
    kind: str
    text: str
    summary: str


class ChatContext(FrozenModel):
    steps: tuple[ContextStep, ...]


def _cut(value: JsonValue, chars: int = _STRING_CHARS) -> JsonValue:
    """``value`` with every string in it cut to ``chars`` characters, an address under one of :data:`_ADDRESS_KEYS` to
    :data:`_ADDRESS_CHARS`."""
    if isinstance(value, str):
        return value[:chars]
    if isinstance(value, list):
        return [_cut(item, chars) for item in value]
    if isinstance(value, dict):
        return {key: _cut(item, _ADDRESS_CHARS if key in _ADDRESS_KEYS else chars) for key, item in value.items()}
    return value


def _steps(events: list[EventRecord]) -> list[ContextStep]:
    """The calls of one run that succeeded, each with its arguments, in the order they ended."""
    calls: dict[str, EventRecord] = {}
    steps: list[ContextStep] = []
    for event in events:
        call_id: JsonValue = event.data.get("call_id")
        if not isinstance(call_id, str):
            continue
        if event.kind == "tool_call":
            calls[call_id] = event
            continue
        call: EventRecord | None = calls.pop(call_id, None)
        tool: JsonValue = event.data.get("tool")
        if call is None or event.data.get("ok") is not True or not isinstance(tool, str):
            continue
        args: JsonValue = call.data.get("args")
        kind: JsonValue = event.data.get("kind")
        memory: bool = kind == _MEMORY_WRITE
        text: JsonValue = event.data.get("text")
        summary: JsonValue = event.data.get("summary")
        steps.append(
            ContextStep(
                tool=tool,
                args=cut if isinstance(args, dict) and isinstance(cut := _cut(args), dict) else {},
                kind=kind if isinstance(kind, str) else "",
                text=text[:_STRING_CHARS] if memory and isinstance(text, str) else "",
                summary=summary[:_STRING_CHARS] if memory and isinstance(summary, str) else "",
            )
        )
    return steps


async def chat_context(store: Store, run: RunRecord) -> ChatContext:
    """The steps of the turns of ``run``'s chat before it, oldest first; none for a delegated run."""
    if run.parent_run_id is not None:
        return ChatContext(steps=())
    turns: list[RunRecord] = await store.chat_turns_until(run.thread_id, run.created_at, CONTEXT_TURNS)
    steps: list[ContextStep] = []
    for turn in reversed(turns):
        if turn.id != run.id:
            steps.extend(_steps(await store.tool_events(turn.id)))
    return ChatContext(steps=tuple(steps))
