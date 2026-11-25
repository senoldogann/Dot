"""What the first step of a run carries to its model, part by part (item 735): the sections of the system prompt, the
schemas of the tools offered by group, and the rest of the conversation by role, in characters and in the tokens
:func:`dotagent.agent.compact.estimate_tokens` counts. Recorded once a run, at its first step, so that what Dot sends
before any work is measured in the owner's own runs (their skills, servers, notes and team), not guessed; `dotx stats`
reads it. A fresh scheduled run of the owner's sent 50,000 to 61,000 tokens before its first step on 2026-10-27, and a
fresh home's sends about 20,000: nothing said where the rest came from."""

import hashlib
from collections.abc import Sequence

from pydantic import Field

from dotagent.agent.compact import estimate_tokens
from dotagent.core.jsonutil import dumps_compact
from dotagent.core.text import CHARS_PER_TOKEN
from dotagent.core.types import FrozenModel
from dotagent.llm.types import ChatRequest, Message
from dotagent.tools.groups import group_of

CONTEXT_PARTS_EVENT: str = "context_parts"
# The name of the part of the system prompt before its first heading.
OPENING: str = "(opening)"
_HEADING: str = "# "
_FENCE: str = "```"
_DIGEST_CHARS: int = 10


class ContextParts(FrozenModel):
    """A request's parts in characters: ``system`` by the heading of each section of the system prompt, ``tools`` by
    group (``core``, ``browser``, ``mcp__<server>``...) with ``tools_offered`` in all, ``history`` by the role of the
    messages after the system prompt; ``tokens`` is the whole request as Dot estimates it. ``system_hashes`` and
    ``tools_hashes`` fingerprint each section and each group's schemas (item 1742), so that a prefix that changed from
    one run of a chat to the next is told by name, not by a size that may not move; a run recorded before them has
    none."""

    system: dict[str, int]
    tools: dict[str, int]
    tools_offered: int
    history: dict[str, int]
    tokens: int
    system_hashes: dict[str, str] = Field(default_factory=dict)
    tools_hashes: dict[str, str] = Field(default_factory=dict)


def _digest(text: str) -> str:
    """A short fingerprint of ``text``: it names a change, it guards nothing."""
    return hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()[:_DIGEST_CHARS]


def _section_texts(prompt: str) -> dict[str, str]:
    """The text of each section of ``prompt``, by its heading, in order; the text before the first heading is
    :data:`OPENING`. Sections of one heading are added together. A line in a fenced block (a skill's or a note's shell
    comment, ``# install``) is no heading: its words would be kept as a section's name."""
    sections: dict[str, str] = {}
    name: str = OPENING
    fenced: bool = False
    for line in prompt.splitlines(keepends=True):
        if line.lstrip().startswith(_FENCE):
            fenced = not fenced
        elif line.startswith(_HEADING) and not fenced:
            name = line.removeprefix(_HEADING).strip()
        sections[name] = sections.get(name, "") + line
    return sections


def _message_chars(message: Message) -> int:
    return (
        len(message.content)
        + len(message.thinking or "")
        + sum(len(call.name) + len(dumps_compact(call.arguments)) for call in message.tool_calls)
    )


def parts_of(request: ChatRequest) -> ContextParts:
    """The parts of ``request``, whose first message is the system prompt."""
    messages: Sequence[Message] = request.messages
    sections: dict[str, str] = _section_texts(messages[0].content) if messages and messages[0].role == "system" else {}
    system: dict[str, int] = {name: len(text) for name, text in sections.items()}
    schemas: dict[str, str] = {}
    for spec in request.tools:
        group: str = group_of(spec.name)
        schemas[group] = schemas.get(group, "") + dumps_compact(spec.model_dump(mode="json"))
    tools: dict[str, int] = {group: len(text) for group, text in schemas.items()}
    history: dict[str, int] = {}
    for message in messages[1:] if system else messages:
        history[message.role] = history.get(message.role, 0) + _message_chars(message)
    return ContextParts(
        system=system,
        tools=tools,
        tools_offered=len(request.tools),
        history=history,
        tokens=estimate_tokens(messages, request.tools),
        system_hashes={name: _digest(text) for name, text in sections.items()},
        tools_hashes={group: _digest(text) for group, text in schemas.items()},
    )


def tokens_of(chars: int) -> int:
    """``chars`` in the tokens Dot's estimate counts."""
    return round(chars / CHARS_PER_TOKEN)
