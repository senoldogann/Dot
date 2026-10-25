"""What the earlier turns of a chat used, read and remembered (item 650), for the context of the Run tab: the panel
keeps the whole chat's from its start, not only the open run's, which a new message used to start again from nothing.
Each earlier turn's tool calls that succeeded, oldest first, each with the arguments the panel reads (a search's words,
a page's address, a skill's name, a note's first words) and, for a change of the memory, what its result says. A string
of the arguments is cut, as the panel shows a line of it at most; a delegated run's calls are its own page's."""

from pydantic import JsonValue

from dotagent.core.types import FrozenModel, JsonObject
from dotagent.store.db import Store
from dotagent.store.models import RunRecord, ToolStep

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


def _steps(rows: list[ToolStep]) -> list[ContextStep]:
    """The calls that succeeded, each with its arguments, in the order they ended."""
    calls: dict[tuple[str, str], ToolStep] = {}
    steps: list[ContextStep] = []
    for row in rows:
        # A row with no call id pairs with nothing (every writer sets one).
        if row.call_id == "":
            continue
        key: tuple[str, str] = (row.run_id, row.call_id)
        if row.kind == "tool_call":
            calls[key] = row
            continue
        call: ToolStep | None = calls.pop(key, None)
        if call is None or not row.ok or row.tool == "":
            continue
        cut: JsonValue = _cut(call.args)
        steps.append(
            ContextStep(
                tool=row.tool,
                args=cut if isinstance(cut, dict) else {},
                kind=row.result_kind,
                text=row.text[:_STRING_CHARS] if row.result_kind == _MEMORY_WRITE else "",
                summary=row.summary[:_STRING_CHARS] if row.result_kind == _MEMORY_WRITE else "",
            )
        )
    return steps


async def chat_context(store: Store, run: RunRecord) -> ChatContext:
    """The steps of the turns of ``run``'s chat before it, oldest first, read in one go; none for a delegated run."""
    if run.parent_run_id is not None:
        return ChatContext(steps=())
    turns: list[RunRecord] = await store.chat_turns_until(run.thread_id, run.created_at, CONTEXT_TURNS)
    earlier: list[str] = [turn.id for turn in reversed(turns) if turn.id != run.id]
    return ChatContext(steps=tuple(_steps(await store.turn_tool_steps(earlier))))
