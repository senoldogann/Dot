"""What an MCP server is for, in the line of its tool group in a run's instructions (item 659).

A server called ``rea-mcp`` or ``grep-app`` says little by its name, and a group that is off shows nothing else of it,
so the line says what it is for, from the first of two places that has words:

1. the ``description`` the owner wrote in its ``[[mcp]]`` table, which is the owner's own text, and the words Dot has
   for the server that comes with it (:mod:`dotagent.mcp.builtin`), which are Dot's: both stand in the line as they
   are;
2. what the server says of itself: the ``instructions`` of its initialize result, else the first sentences of the
   descriptions of its first tools. These are third-party words in the text a run trusts most, so the line shows them
   as the server's claim, in quotes, and what is shown is made harmless by its shape and not by what it says
   (:func:`server_words`):

   - one line: every line break, of any kind, is a space, and a heading is dropped, so the words cannot begin a line,
     a heading, a list item or a fence of the instructions;
   - plain: no angle or square bracket, brace, bar, backtick or backslash, no double quote and no run of rule marks, so
     they cannot close the quote they stand in, open a tag (``<mcp_content>``), a note of the system (``[system
     note]``) or a link; no address;
   - nothing that prints nothing (a zero-width or bidirectional control, which could hide or reorder words);
   - no secret Dot holds, masked wherever it stands before the words are cut;
   - 160 characters at most.

   What no shape can hold is that the words are true, or that they ask nothing of the model: the instructions say so
   (``Tool groups`` in :mod:`dotagent.agent.prompt`) and the policy judges every call the words might steer the model
   to, as it judges any other.

Everything here is pure: it works on what the hub learned when the server connected (:mod:`dotagent.mcp.hub`) and
never reaches a server.
"""

import re
import unicodedata
from collections.abc import Sequence

from dotagent.config import McpServerConfig
from dotagent.core.learned_text import LINE_BREAKS, without_hidden
from dotagent.core.text import KNOWN_SECRET_MIN_CHARS, plain_line, redact_known
from dotagent.mcp.types import McpPurpose, McpTool

# The most characters a server's words take in a line, the cut's "…" included.
SERVER_WORDS_CHARS: int = 160
# How much of what a server sent is read: its first lines say what it is, and a server could send any amount.
READ_CHARS: int = 4000
# The tools whose descriptions a server with no instructions is summed up by.
SUMMARY_TOOLS: int = 4

_HEADING: re.Pattern[str] = re.compile(r"^[ \t>]*#{1,6}(?:[ \t]|$)")
_DOUBLE_QUOTES: re.Pattern[str] = re.compile('["“”„‟«»]')
_STRUCTURE: re.Pattern[str] = re.compile(r"[<>\[\]{}|\\`]")
# Three of the same mark in a row: a rule, an underline, a fence.
_RULE: re.Pattern[str] = re.compile(r"([-=*_~#+])\1{2,}")
# An address with its scheme, or ``www.``: words that tell what a server is for need none, and an address is a place a
# model could be sent to.
_ADDRESS: re.Pattern[str] = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://\S*|\bwww\.\S+", re.IGNORECASE)
_SENTENCE_END: re.Pattern[str] = re.compile(r"[.!?]+(?=\s+\S)")


def clean(text: str) -> str:
    """``text`` as one plain line (the shape the module's docstring lists), at most :data:`READ_CHARS` of it looked at.
    A text of nothing but headings keeps their words. The marks of Markdown go as
    :func:`dotagent.core.text.plain_line` takes them, a pair of underscores among them (``mcp__a__b`` is ``mcpab``)."""
    folded: str = unicodedata.normalize("NFKC", text[:READ_CHARS])
    lines: list[str] = without_hidden(LINE_BREAKS.sub("\n", folded)).split("\n")
    kept: list[str] = [line for line in lines if _HEADING.match(line) is None] or lines
    ruled: str = _RULE.sub(" ", "\n".join(kept))
    plain: str = plain_line(ruled, len(ruled))
    unquoted: str = _DOUBLE_QUOTES.sub("'", plain)
    return " ".join(_STRUCTURE.sub(" ", _ADDRESS.sub(" ", unquoted)).split())


def sentences(flat: str) -> list[str]:
    """The sentences of a line: each ends at a full stop, a question mark or an exclamation mark that is followed by a
    space and a capital letter or a digit (``e.g. a list`` and ``v1.2`` do not end one)."""
    found: list[str] = []
    start: int = 0
    for match in _SENTENCE_END.finditer(flat):
        following: str = flat[match.end() :].lstrip()[0]
        if following.isupper() or following.isdigit():
            found.append(flat[start : match.end()].strip())
            start = match.end()
    last: str = flat[start:].strip()
    if last:
        found.append(last)
    return found


def _cut(text: str, limit: int) -> str:
    """``text`` of at most ``limit`` characters: as it is when it fits, else cut at a word and ended with ``…``."""
    if len(text) <= limit:
        return text
    room: str = text[: limit - 1]
    space: int = room.rfind(" ")
    if space >= limit * 3 // 4:
        room = room[:space]
    return room.rstrip(" ,;:-") + "…"


def _gist(parts: Sequence[str]) -> str:
    """The first of ``parts`` (sentences) that fit :data:`SERVER_WORDS_CHARS` together, whole; when not even the first
    does, its start."""
    gist: str = ""
    for part in parts:
        joined: str = f"{gist} {part}" if gist else part
        if len(joined) > SERVER_WORDS_CHARS:
            break
        gist = joined
    return gist or _cut(parts[0] if parts else "", SERVER_WORDS_CHARS)


def _tool_sentences(tools: Sequence[McpTool], held: Sequence[str]) -> str:
    """The first sentence of the description of each of the first :data:`SUMMARY_TOOLS` tools that has one of its own
    (a tool with none is described by its name, which the line of the group names anyway), each as a sentence."""
    firsts: list[str] = []
    for tool in tools:
        if tool.description.strip() in ("", tool.remote_name):
            continue
        first: str = next(iter(sentences(_masked(tool.description, held))), "").rstrip(".!? ")
        if first and first not in firsts:
            firsts.append(first)
        if len(firsts) == SUMMARY_TOOLS:
            break
    return "".join(f"{first}. " for first in firsts).strip()


# What a held secret is replaced with before the words are cleaned: a word the cleaning leaves as it is, which
# becomes the mask after.
_HELD: str = "HELDSECRETMASK"


def _masked(source: str, held: Sequence[str]) -> str:
    """``source`` cleaned (:func:`clean`) with every secret of ``held`` masked: before the cleaning, as the secret
    stands in it (cleaning takes out marks a key may hold, ``__`` or ``|``, and the key would no longer be found), and
    after, as a key split by a character that prints nothing stands whole only once that character is gone."""
    # A mask already there (the tools' sentences, masked once) stays one: the cleaning would take ``***`` for a rule.
    marked: str = source.replace("***", _HELD)
    for value in sorted(set(held), key=len, reverse=True):
        if len(value) >= KNOWN_SECRET_MIN_CHARS:
            marked = marked.replace(value, _HELD)
    return redact_known(clean(marked), held).replace(_HELD, "***")


def server_words(instructions: str | None, tools: Sequence[McpTool], held: Sequence[str]) -> str:
    """What a server says of itself, made fit to stand in quotes in the line of its group: the start of its
    ``instructions``, else the first sentences of the descriptions of its ``tools``, as one plain line (:func:`clean`)
    with the secrets ``held`` masked, whole sentences up to :data:`SERVER_WORDS_CHARS` characters. ``""`` for a server
    that says nothing."""
    for source in (instructions or "", _tool_sentences(tools, held)):
        parts: list[str] = sentences(_masked(source, held))
        if parts:
            return _gist(parts)
    return ""


def purpose_of(
    config: McpServerConfig, instructions: str | None, tools: Sequence[McpTool], held: Sequence[str]
) -> McpPurpose | None:
    """The purpose of the group of the server ``config``, which said ``instructions`` when it connected and offers
    ``tools``: the description of its table (the owner's, or Dot's for a server that comes with it) when it has one,
    else the server's own words (:func:`server_words`), as its claim. ``None`` for a server with neither."""
    if config.description:
        return McpPurpose(text=config.description, claimed=False)
    words: str = server_words(instructions, tools, held)
    return McpPurpose(text=words, claimed=True) if words else None
