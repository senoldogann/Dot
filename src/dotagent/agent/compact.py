"""Context management: token estimates, trimming of stale output and repair of broken tool pairing."""

from collections.abc import Sequence

from dotagent.core.jsonutil import dumps_compact
from dotagent.core.text import truncate_middle
from dotagent.llm.types import Message, ToolSpec

# Characters per token in the estimate; the loop calibrates the estimate against the provider's own counts.
CHARS_PER_TOKEN: float = 3.2
_TOKENS_PER_IMAGE: int = 1400
_OLD_RESULT_CHARS: int = 700
_OMITTED_IMAGE_NOTE: str = "\n[screenshot omitted: it is out of date]"
_TRANSCRIPT_RESULT_CHARS: int = 1500
_MISSING_RESULT: str = "[no result was recorded: the run was interrupted before this tool finished]"


def estimate_tokens(messages: Sequence[Message], tools: Sequence[ToolSpec]) -> int:
    """A cheap overestimate of the prompt size; calibrated against provider usage by the caller."""
    characters: int = sum(len(dumps_compact(tool.model_dump(mode="json"))) for tool in tools)
    images: int = 0
    for message in messages:
        characters += len(message.content) + len(message.thinking or "")
        for call in message.tool_calls:
            characters += len(call.name) + len(dumps_compact(call.arguments))
        images += len(message.images)
    return round(characters / CHARS_PER_TOKEN) + images * _TOKENS_PER_IMAGE


def drop_old_images(messages: Sequence[Message], keep_recent: int) -> list[Message]:
    """Keep only the newest ``keep_recent`` images; older ones become a short note in the message text."""
    holders: list[int] = [index for index, message in enumerate(messages) if message.images]
    stale: set[int] = set(holders[: max(0, len(holders) - keep_recent)])
    return [
        message.model_copy(update={"images": (), "content": message.content + _OMITTED_IMAGE_NOTE})
        if index in stale
        else message
        for index, message in enumerate(messages)
    ]


def shrink_old_tool_results(messages: Sequence[Message], keep_recent: int) -> list[Message]:
    """Truncate tool output that is older than the newest ``keep_recent`` tool messages."""
    results: list[int] = [index for index, message in enumerate(messages) if message.role == "tool"]
    stale: set[int] = set(results[: max(0, len(results) - keep_recent)])
    return [
        message.model_copy(update={"content": truncate_middle(message.content, _OLD_RESULT_CHARS)})
        if index in stale and len(message.content) > _OLD_RESULT_CHARS
        else message
        for index, message in enumerate(messages)
    ]


def trim_history(messages: Sequence[Message], keep_recent_results: int, keep_recent_images: int) -> list[Message]:
    return shrink_old_tool_results(drop_old_images(messages, keep_recent_images), keep_recent_results)


def repair_history(messages: Sequence[Message]) -> list[Message]:
    """Make tool calls and tool results pair up again, as providers require.

    A tool result whose call is missing is dropped; a call without a result gets a placeholder result
    directly after its assistant message.
    """
    repaired: list[Message] = []
    open_calls: dict[str, Message] = {}
    pending: list[Message] = []

    def flush_pending() -> None:
        for call_id, owner in open_calls.items():
            call_name: str = next(call.name for call in owner.tool_calls if call.id == call_id)
            repaired.append(
                Message(
                    role="tool",
                    content=_MISSING_RESULT,
                    images=(),
                    tool_calls=(),
                    tool_call_id=call_id,
                    tool_name=call_name,
                    thinking=None,
                    raw=(),
                    raw_origin=None,
                )
            )
        open_calls.clear()
        repaired.extend(pending)
        pending.clear()

    for message in messages:
        if message.role == "tool":
            if message.tool_call_id is not None and message.tool_call_id in open_calls:
                repaired.append(message)
                del open_calls[message.tool_call_id]
            continue
        if open_calls:
            flush_pending()
        repaired.append(message)
        if message.role == "assistant" and message.tool_calls:
            open_calls = {call.id: message for call in message.tool_calls}
    if open_calls:
        flush_pending()
    return repaired


def split_for_summary(
    messages: Sequence[Message], keep_tail: int, max_tail_tokens: int
) -> tuple[list[Message], list[Message]]:
    """Split into (older part to summarise, recent part to keep) at a point that never separates a tool exchange.

    The recent part keeps about the newest ``keep_tail`` messages (more when the cut would fall inside a tool exchange)
    and fewer when those come to more than ``max_tail_tokens`` estimated tokens, but never less than the newest
    exchange: the newest assistant message and what follows it. A history without one has nothing to summarise.
    """
    newest: int = max((index for index, message in enumerate(messages) if message.role == "assistant"), default=0)
    cut: int = max(0, len(messages) - keep_tail)
    while cut > 0 and messages[cut].role == "tool":
        cut -= 1
    cut = min(cut, newest)
    while cut < newest and (messages[cut].role == "tool" or estimate_tokens(messages[cut:], ()) > max_tail_tokens):
        cut += 1
    return list(messages[:cut]), list(messages[cut:])


def render_transcript(messages: Sequence[Message]) -> str:
    """A plain-text rendering of messages for the summariser; tool output is shortened."""
    lines: list[str] = []
    for message in messages:
        if message.role == "assistant":
            if message.content:
                lines.append(f"ASSISTANT: {message.content}")
            for call in message.tool_calls:
                lines.append(f"ASSISTANT CALLS {call.name}: {truncate_middle(dumps_compact(call.arguments), 500)}")
        elif message.role == "tool":
            lines.append(
                f"TOOL RESULT ({message.tool_name}): {truncate_middle(message.content, _TRANSCRIPT_RESULT_CHARS)}"
            )
        else:
            lines.append(f"{message.role.upper()}: {message.content}")
    return "\n".join(lines)
