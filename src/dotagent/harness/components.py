"""The components of a version of the harness (item 389, docs/harness.md): the texts a run's system prompt takes from
the adopted version, and what makes a text no component. Two for now: habits (how to work here, after how to work) and
tool guidance (what earlier runs learned of the tools on this Mac, after the tools). Both are plain text the owner
reads on one screen, bounded in size, with nothing in them that must not stand in a prompt for good: whatever the one
gate for learned texts refuses (:mod:`dotagent.core.learned_text`, item 587): a secret, a link, an e-mail address, a
path in a home folder, an account name (any of which a page a run read may have put into a proposal; the bounds of a
lesson's, :mod:`dotagent.agent.lessons`), a line that imitates a heading of the prompt or a note of the system, or a
character that does not print, which could hide a line from the owner who reads the text."""

import re
from collections.abc import Sequence
from typing import Literal

from dotagent.core.learned_text import LINE_BREAKS, finds_handle, finds_link, gate
from dotagent.core.types import FrozenModel

type Component = Literal["habits", "tool_guidance"]
COMPONENTS: tuple[Component, ...] = ("habits", "tool_guidance")
# The longest line a component may hold: a rule is a sentence or two, never a page.
LINE_CHARS: int = 300


class HarnessText(FrozenModel):
    """What the adopted version adds to a run's prompt; an empty component adds nothing."""

    habits: str
    tool_guidance: str

    def component(self, name: Component) -> str:
        return self.habits if name == "habits" else self.tool_guidance


class GivenText(HarnessText):
    """A text as a model may be given it (item 587, pre-push review round 2): what
    :func:`dotagent.harness.service.given_text` made of a stored version, after the gate for learned texts was asked of
    it again (a component it refuses is left out), or Dot's own text alone. A probe's ask, the installation of a probed
    text, the prompt's inputs, the evals' runner and the comparison of a lesson with the rules in use take this type
    and no other, so that a text taken as it is stored (:func:`dotagent.harness.service.raw_text_of`) does not pass the
    type checker there, and a probe's ask made of one is refused when it is made. It is built in two places, which a
    test reads the source for: ``NO_HARNESS`` below and ``given_text``; a probe's ask read back from the file the step
    wrote holds one too."""


# Dot's own text alone: what a run takes while no version is adopted.
NO_HARNESS: GivenText = GivenText(habits="", tool_guidance="")
# The mark before a rule of a component that is a list: a bullet or a number.
_LIST_MARK: re.Pattern[str] = re.compile(r"^\s*(?:[-*•]|\d{1,2}[.)])\s+")


def rules_of(text: GivenText) -> tuple[str, ...]:
    """The rules the adopted ``text`` gives a run, one line each, the habits first and then the tool guidance, a
    bullet or a number before a rule taken off and the blank lines left out: what a lesson is compared with, so that
    the memory does not learn again what the prompt already says (item 584)."""
    return tuple(
        line
        for name in COMPONENTS
        for raw in text.component(name).splitlines()
        if (line := _LIST_MARK.sub("", raw).strip())
    )


def normalized(text: str) -> str:
    """``text`` as a component is kept: line breaks as newlines, no trailing spaces on a line, at most one blank line in
    a row and none at either end."""
    lines: list[str] = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    kept: list[str] = []
    for line in lines:
        if line == "" and (not kept or kept[-1] == ""):
            continue
        kept.append(line)
    while kept and kept[-1] == "":
        kept.pop()
    return "\n".join(kept)


def names_a_task(text: str, task_ids: Sequence[str]) -> str | None:
    """The first of ``task_ids`` (the eval tasks of the scoring set and of the final set) that ``text`` names, or
    ``None``: the id as the catalog writes it (``bump-version``), in any case, as a token of its own (item 588). What a
    candidate is judged on is never shown to the model that writes it, so an experience that names such a task cannot
    stand in the library the proposer reads; the words of its description are no name (``bump version``, ``report
    failing tests`` and ``read config port`` are what an experience may say of a version, a test run and a port), and
    an experience is never refused for them."""
    flat: str = text.casefold()
    for task_id in task_ids:
        if task_id and re.search(rf"(?<![\w-]){re.escape(task_id.casefold())}(?![\w-])", flat):
            return task_id
    return None


def description_problem(text: str, secrets: Sequence[str]) -> str | None:
    """Why ``text``, which a model wrote about a step of the improvement loop (a moment of a run, a decision of the
    composer, the summary of a version), cannot be recorded, or ``None`` (item 587, review round 1). These describe what
    happened and no run is told to follow them, so a site may be named bare, as a lesson of a clean run may; the rest is
    asked as every learned text is (:func:`dotagent.core.learned_text.gate`: a character that does not print, a secret,
    an e-mail address, a path in a home folder, the owner's mark, a line that imitates the prompt), and a link to go to
    and an account name are refused as a lesson of a run that read outside content refuses them."""
    problem: str | None = gate(text, secrets, addresses=False)
    if problem is not None:
        return problem
    if finds_link(text, naming=False):
        return "a link"
    return "an account name" if finds_handle(text) else None


def component_problem(text: str, max_chars: int, max_lines: int, secrets: Sequence[str]) -> str | None:
    """Why ``text`` cannot be a component, or ``None`` when it can: longer than ``max_chars``, more than ``max_lines``
    lines (a line ends at any of :data:`dotagent.core.learned_text.LINE_BREAKS`), a line past :data:`LINE_CHARS`, or
    whatever the gate for learned texts refuses (:func:`dotagent.core.learned_text.gate`): a character that does not
    print, a secret (one of ``secrets``, the values Dot holds, or a key of a known form), an e-mail address, a link, a
    path in a home folder, an account name, or a line that imitates the prompt."""
    if len(text) > max_chars:
        return f"too long: {len(text)} characters, the limit is {max_chars}"
    lines: list[str] = LINE_BREAKS.split(text) if text else []
    if len(lines) > max_lines:
        return f"too many lines: {len(lines)}, the limit is {max_lines}"
    long_line: int | None = next((number for number, line in enumerate(lines, start=1) if len(line) > LINE_CHARS), None)
    if long_line is not None:
        return f"line {long_line} is longer than {LINE_CHARS} characters"
    return gate(text, secrets)
