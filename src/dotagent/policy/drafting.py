"""A rule in the owner's own words (item 110): the review model drafts its parts, the pattern is written here.

The model names the tool, the answer and what an action must say for the rule to apply: for the shell, the commands the
rule is about; for any tool, words or addresses the action mentions (:class:`RuleDraft`, asked for by
:func:`dotagent.agent.review.rule_drafter_for`). The pattern is written from those parts, never by the model, so that it
fits what :func:`dotagent.policy.engine.match_rule` searches (the action's one-line summary, a line break and its
target) and errs on the side of the answer: a rule that lets Dot act fits a command only when it runs alone with plain
arguments, after a ``cd`` at most, and a rule that stops Dot fits a command or a word wherever the action mentions it.
The rule's description is the sentence it reads back as, written here too, so that the list of rules says in words
what each drafted pattern fits.
"""

import fnmatch
import re
from collections.abc import Sequence
from typing import Annotated

import regex
from pydantic import Field, StringConstraints

from dotagent.core.errors import InvalidValueError
from dotagent.core.types import FrozenModel
from dotagent.store.models import Behavior

SHELL_TOOL: str = "shell"
# The AppleScript tool runs shell lines too (``do shell script``): for a rule that lets Dot act it is the shell's twin.
APPLE_SCRIPT_TOOL: str = "apple_script"
# The answers that let Dot act; a rule with any other answer stops it.
_LETTING: frozenset[Behavior] = frozenset({"allow", "preapproved"})
# What chains a command to another in zsh (which runs the shell tool's commands), substitutes one into it, sends its
# output on or starts a second line: a command that a rule lets Dot run holds none of them, nor do its arguments.
_UNPLAIN: frozenset[str] = frozenset("\n\r;&|`$<>()")
_PLAIN_CHAR: str = r"[^\n\r;&|`$<>()]"
# The one command that may come first: the shell tool's commands often change into a folder and chain the step with &&.
_CD_FIRST: str = rf"(?:cd[ \t]+{_PLAIN_CHAR}+&&[ \t]*)?"
# A word character as the engine that matches a rule takes it (``regex``, in ``policy.engine``): Python's ``re`` takes
# ``²`` and ``½`` for word characters and a circled letter or a vowel sign for none, and a boundary put where the engine
# sees none never fits, so that a rule that stops Dot at ``x²`` stopped it at nothing (item 629).
_WORD: regex.Pattern[str] = regex.compile(r"\w")
# What a command or a mention must hold for a rule to match on: a letter, a number or ``_``. The engine's ``\w`` takes
# a joiner, a variation selector and a combining mark as well, which nobody reads as a word: a mention of them alone
# stays refused (item 629).
_HAS_WORD: regex.Pattern[str] = regex.compile(r"[\p{L}\p{N}_]")
# What a rule's description may hold (``RuleRequest``).
_DESCRIPTION_CHARS: int = 300
_ANSWER_TEXT: dict[Behavior, str] = {
    "allow": "Dot does it without asking",
    "preapproved": "Dot does it without asking, even after reading untrusted content",
    "ask": "Dot asks you first",
    "handoff": "Dot hands it to you",
}


class RuleDraft(FrozenModel):
    """The parts of a rule as the review model drafts them from the owner's words."""

    tool: Annotated[str, StringConstraints(strip_whitespace=True, max_length=120)] = Field(
        description=(
            "The tool the rule is about, by its exact name from the list, or a pattern with * for a family of tools: "
            "browser_* for every browser tool, * for every tool."
        )
    )
    answer: Behavior = Field(
        description=(
            "allow: Dot does it without asking (it still asks after reading untrusted content when the action reaches "
            "outside the Mac). preapproved: without asking even then. ask: Dot asks the owner first. handoff: Dot "
            "stops and the owner does the step."
        )
    )
    commands: Annotated[
        list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]],
        Field(max_length=10),
    ] = Field(
        description=(
            "For the shell tool only: the commands the rule is about, as they are typed (git status). Empty for any "
            "other tool and when mentions are given."
        )
    )
    mentions: Annotated[
        list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]],
        Field(max_length=10),
    ] = Field(
        description=(
            "The words, names or addresses an action must mention for the rule to apply (mybank.com, invoice). Empty "
            "when the rule is about every use of the tool and when commands are given. A rule that lets Dot act and "
            "covers the shell tool (shell, or a pattern such as *) gives commands, never mentions; one that covers the "
            "AppleScript tool (apple_script, or a pattern such as *) gives no mentions either."
        )
    )
    problem: Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)] = Field(
        description=(
            "Empty when the parts above are a rule for the owner's words, a broad one too. Otherwise one sentence in "
            "English saying why no rule can be made of them; never a note or a warning about a rule that can be made."
        )
    )


class DraftedRule(FrozenModel):
    """A rule drafted from the owner's words, as the rule form takes it; its description is the sentence it reads
    back as."""

    behavior: Behavior
    tool_glob: str
    match_regex: str
    description: str


def command_pattern(commands: Sequence[str]) -> str:
    """A pattern that fits a shell action whose command line is one of ``commands`` with plain arguments, alone or
    after a ``cd``: nothing chained to it or substituted into it, its output sent nowhere, one line."""
    names: str = "|".join(_spaced(command) for command in commands)
    return rf"\A[^\n]*\n[ \t]*{_CD_FIRST}(?:{names})(?:[ \t]{_PLAIN_CHAR}*)?\Z"


def mention_pattern(mentions: Sequence[str]) -> str:
    """A pattern that fits an action that mentions one of ``mentions`` in its summary or its target, as a whole word
    where a mention starts or ends with a word character as the engine that matches rules tells one (a letter, a mark,
    a decimal digit, a connector such as ``_`` or ``‿``, a joiner; not ``²`` or ``½``): x.com does not fit netflix.com
    (item 629)."""
    return "(?:" + "|".join(_bounded(mention) for mention in mentions) + ")"


def _spaced(text: str) -> str:
    """``text`` as a pattern in which each run of spaces fits any run of spaces and tabs."""
    return r"[ \t]+".join(re.escape(word) for word in text.split())


def _bounded(mention: str) -> str:
    start: str = r"\b" if _WORD.match(mention) else ""
    end: str = r"\b" if _WORD.match(mention[-1]) else ""
    return f"{start}{_spaced(mention)}{end}"


def _either(items: Sequence[str]) -> str:
    quoted: list[str] = [f"“{item}”" for item in items]
    return quoted[0] if len(quoted) == 1 else f"{', '.join(quoted[:-1])} or {quoted[-1]}"


def _tools_text(tool_glob: str, fitting: Sequence[str]) -> str:
    if tool_glob == "*":
        return "any tool"
    if list(fitting) == [tool_glob]:
        return tool_glob
    return f"{tool_glob} ({len(fitting)} {'tool' if len(fitting) == 1 else 'tools'})"


def rule_from_draft(draft: RuleDraft, tools: frozenset[str]) -> DraftedRule:
    """The rule ``draft`` makes for a Dot that has ``tools``. A draft whose tool fits none of them, that gives commands
    for another tool than the shell or together with mentions, that lets Dot run a command that is not one plain
    command, that lets Dot act on words in a tool that is or includes the shell or the AppleScript tool, or whose
    command or mention holds no word to match on, is refused with what is wrong with it. What the draft says in
    ``problem`` is the caller's to read first."""
    fitting: list[str] = sorted(name for name in tools if fnmatch.fnmatchcase(name, draft.tool))
    if not fitting:
        raise InvalidValueError("the tool fits none of Dot's tools", {"tool": draft.tool})
    if draft.commands and draft.mentions:
        raise InvalidValueError(
            "a rule names commands or mentions, not both",
            {"commands": ", ".join(draft.commands), "mentions": ", ".join(draft.mentions)},
        )
    if draft.commands and draft.tool != SHELL_TOOL:
        raise InvalidValueError(f"only a rule for the {SHELL_TOOL} tool names commands", {"tool": draft.tool})
    for part in (*draft.commands, *draft.mentions):
        if _HAS_WORD.search(part) is None:
            raise InvalidValueError("a command or a mention needs a word to match on", {"part": part})
    letting: bool = draft.answer in _LETTING
    if letting and draft.mentions and fnmatch.fnmatchcase(SHELL_TOOL, draft.tool):
        # A word fits anywhere in a command line, so a rule that lets Dot act on a word would let the command with
        # anything chained to it through as well; for the shell it is the command that is named, and it fits alone
        # (item 561).
        raise InvalidValueError(
            f"a rule that lets Dot act and covers the {SHELL_TOOL} tool names commands, not words: a word fits "
            "anywhere in a command line, one with more chained to it too",
            {"tool": draft.tool, "mentions": ", ".join(draft.mentions)},
        )
    if letting and draft.mentions and fnmatch.fnmatchcase(APPLE_SCRIPT_TOOL, draft.tool):
        # The same for the AppleScript tool, which runs shell lines through ``do shell script``: a word fits anywhere
        # in a script, one that runs a command outside the workspace too. It has no form for commands (review of 561).
        raise InvalidValueError(
            f"a rule that lets Dot act and covers the {APPLE_SCRIPT_TOOL} tool names no words: a word fits anywhere in "
            "a script, one that runs a shell line with do shell script too",
            {"tool": draft.tool, "mentions": ", ".join(draft.mentions)},
        )
    if letting:
        for command in draft.commands:
            if not _UNPLAIN.isdisjoint(command):
                raise InvalidValueError(
                    "a command that a rule lets Dot run is one plain command, without ; & | ` $ < > ( ) or a line "
                    "break",
                    {"command": command},
                )
    pattern: str
    scope: str
    if draft.commands and letting:
        pattern = command_pattern(draft.commands)
        scope = f"{_either(draft.commands)} run alone in the shell, with plain arguments (after a cd at most)"
    elif draft.commands:
        pattern = mention_pattern(draft.commands)
        scope = f"any shell command that mentions {_either(draft.commands)}"
    elif draft.mentions:
        pattern = mention_pattern(draft.mentions)
        scope = f"any use of {_tools_text(draft.tool, fitting)} that mentions {_either(draft.mentions)}"
    else:
        pattern = ""
        scope = f"every use of {_tools_text(draft.tool, fitting)}"
    sentence: str = f"For {scope}: {_ANSWER_TEXT[draft.answer]}."
    return DraftedRule(
        behavior=draft.answer,
        tool_glob=draft.tool,
        match_regex=pattern,
        description=sentence if len(sentence) <= _DESCRIPTION_CHARS else f"{sentence[: _DESCRIPTION_CHARS - 1]}…",
    )
