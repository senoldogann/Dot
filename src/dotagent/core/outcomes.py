"""What a step of a batch came to, beyond its being sent (item 1145): whether it went out at all, and the checks made
of its outcome, each verified, contradicted or uncertain. One contract for the browser and the desktop, whose adapters
make the observations; everything here is pure.

A check never carries what a field holds, only how much of it: a password typed into a plain field is no secret field
to the page, and a step's description shows its text by its length alone for the same reason. ``verified`` says that
the page or the window showed it, not that a server took it.
"""

from collections.abc import Sequence
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from dotagent.core.types import FrozenModel

# How a check of an outcome ended. ``verified``: it was seen to hold after the step. ``contradicted``: a state that is
# read at once was read and is not what was asked (a field still holds what it held, a box is not ticked).
# ``uncertain``: no decision: a change that was waited for did not show in time, which does not say that it will not
# come, or a value came out other than written.
type CheckStatus = Literal["verified", "contradicted", "uncertain"]
# Whether a step went out to the page or the application: ``unknown`` for a step that failed where it cannot be told.
type Dispatch = Literal["sent", "not_sent", "unknown"]
type StepOutcome = Literal["done", "failed", "skipped"]
# What ended the wait for something a step was expected to show.
type SightEnd = Literal["seen", "timeout", "still", "dialog", "no_time"]

# The fewest characters of a text a step expects to see: a shorter one is in too many other words ("ok" in "book").
EXPECT_MIN_CHARS: int = 4
EXPECT_MAX_CHARS: int = 200

# The fewest letters and digits of a written text that count as found at the start or the end of what a field holds:
# a shorter one ends too many other words ("a" ends "banana").
_ADDED_TO_MIN_CHARS: int = 3
_HOLDS_TEXT: str = "the field holds the text written"
_TOOK_TEXT: str = "the field took the text written"
_AS_WRITTEN: str = "as written"
_FORMATTED: str = "formatted by the page"
_UNCHANGED: str = "it still holds what it held before the step"
_SENT: dict[Dispatch, str] = {"sent": " (sent)", "not_sent": " (not sent)", "unknown": " (may have been sent)"}
_NOT_CHECKED: str = " [not checked]"


class OutcomeCheck(FrozenModel):
    """One condition checked after a step: what was checked, in Dot's words; how it ended; what was observed, never a
    field's value; and, when it is not verified, why and what to do next."""

    condition: str
    status: CheckStatus
    evidence: str
    reason: str


class StepReport(FrozenModel):
    """One step of a batch. ``outcome`` is what it was before item 1145: done once the step went through, failed with
    why in ``detail``, skipped when the batch stopped before it. ``dispatched`` says whether the step went out, which
    a failure does not tell, and ``checks`` what was checked of its outcome: none is not verified, it is not checked.
    """

    index: int
    description: str
    outcome: StepOutcome
    detail: str
    dispatched: Dispatch
    checks: tuple[OutcomeCheck, ...] = ()


def _plain(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def written_core(text: str) -> str:
    """``text`` as its letters and digits alone, in one case: how a page writes a number, a date or a name (``(555)
    123-4567``, ``ADA``) does not make it another text."""
    return "".join(char for char in text.casefold() if char.isalnum())


def _verified(condition: str, evidence: str) -> OutcomeCheck:
    return OutcomeCheck(condition=condition, status="verified", evidence=evidence, reason="")


def _added_to(core: str, held: str) -> bool:
    """Whether what a field holds (``held``, its letters and digits) is the written text (``core``) with more before
    or after it: an address the page put ``https://`` before, an amount it put a unit after."""
    return len(core) >= _ADDED_TO_MIN_CHARS and (held.startswith(core) or held.endswith(core))


def judge_replaced(before: str, after: str, written: str) -> OutcomeCheck:
    """What a field holds once a step replaced its value with ``written`` (a fill): verified when it holds the text,
    as written, as a page formats it, or with what a page adds before or after it (item 1149: an address field that
    puts ``https://`` first was taken for one that changed the text, in the owner's run), whether or not it held
    that before the step, since a field filled again with the text it holds is as it was; contradicted when it is as
    it was and that is not the text; uncertain when it holds something else (cut short, replaced), told by its length
    alone."""
    if _plain(after) == _plain(written):
        return _verified(_HOLDS_TEXT, _AS_WRITTEN)
    core: str = written_core(written)
    if core and written_core(after) == core:
        return _verified(_HOLDS_TEXT, _FORMATTED)
    if _added_to(core, written_core(after)):
        return _verified(
            _HOLDS_TEXT, f"the page added to it: {len(after)} characters where {len(written)} were written"
        )
    if after == before:
        return OutcomeCheck(
            condition=_HOLDS_TEXT,
            status="contradicted",
            evidence=_UNCHANGED,
            reason="the field did not take the text: it may be read-only or the page put its value back; look at it",
        )
    return OutcomeCheck(
        condition=_HOLDS_TEXT,
        status="uncertain",
        evidence=f"it holds {len(after)} characters where {len(written)} were written",
        reason="the page changed or cut what was written: read the field before going on",
    )


def judge_added(before: str, after: str, written: str) -> OutcomeCheck:
    """What a field holds once a step put ``written`` in at its caret (keys, a paste): verified when the text is in
    it once more than before, as written or as a page formats it, so that a text the field held already does not
    count; contradicted when the field is as it was; uncertain when it changed some other way."""
    plain: str = _plain(written)
    if plain and _plain(after).count(plain) > _plain(before).count(plain):
        return _verified(_TOOK_TEXT, _AS_WRITTEN)
    core: str = written_core(written)
    if core and written_core(after).count(core) > written_core(before).count(core):
        return _verified(_TOOK_TEXT, _FORMATTED)
    if after == before:
        return OutcomeCheck(
            condition=_TOOK_TEXT,
            status="contradicted",
            evidence=_UNCHANGED,
            reason="the field did not take the text: look at the field before typing again, so nothing is typed twice",
        )
    return OutcomeCheck(
        condition=_TOOK_TEXT,
        status="uncertain",
        evidence=f"it holds {len(after)} characters, {len(before)} before the step; {len(written)} were written",
        reason="the page changed or cut what was written: read the field before going on",
    )


def still_on_its_way(check: OutcomeCheck, waited_s: float) -> OutcomeCheck:
    """``check`` where what was sent reaches its field later than the step returns (keys to an application): a field
    that is as it was has not been shown to refuse them, so a contradiction is no more than uncertain."""
    if check.status != "contradicted":
        return check
    return check.model_copy(
        update={
            "status": "uncertain",
            "evidence": f"{check.evidence}, {waited_s:g} s after the keys were sent",
            "reason": "the keys may still be on their way or the field refused them: read the field before typing "
            "again, so nothing is typed twice",
        }
    )


def judge_ticked(asked: bool, now: bool) -> OutcomeCheck:
    """A box once a step ticked or cleared it, read after the page took its turn."""
    wanted: str = "ticked" if asked else "clear"
    if now == asked:
        return _verified(f"the box is {wanted}", "read after the step")
    return OutcomeCheck(
        condition=f"the box is {wanted}",
        status="contradicted",
        evidence=f"it is {'ticked' if now else 'clear'} after the step",
        reason="the page put the box back: look at the page before going on",
    )


def judge_chosen(chosen: bool, unchanged: bool) -> OutcomeCheck:
    """A dropdown once a step chose an option in it: ``chosen`` when the option it shows chosen is the one asked for,
    ``unchanged`` when it shows what it showed before the step."""
    condition: str = "the dropdown shows the option chosen"
    if chosen:
        return _verified(condition, "read after the step")
    if unchanged:
        return OutcomeCheck(
            condition=condition,
            status="contradicted",
            evidence="it still shows the option it showed before the step",
            reason="the dropdown did not keep the option: look at the page before going on",
        )
    return OutcomeCheck(
        condition=condition,
        status="uncertain",
        evidence="it shows another option than the one asked for",
        reason="the page chose another option: read the dropdown before going on",
    )


def judge_sight(condition: str, end: SightEnd, waited_s: float, look_again: str) -> OutcomeCheck:
    """What a step was expected to show, once the wait for it ended. Seen is verified. Not seen is uncertain, never
    contradicted: the wait is bounded and what was sent may still land. ``look_again`` names how to look once more,
    which is what to do in place of sending the step again."""
    if end == "seen":
        return _verified(condition, f"seen after {waited_s:.1f} s")
    evidence: str = {
        "timeout": f"not seen within {waited_s:.1f} s",
        "still": f"not seen after {waited_s:.1f} s, and nothing changed any more",
        "dialog": f"not seen after {waited_s:.1f} s: a dialog opened first",
        "no_time": "not seen at the one look this batch had time left for",
    }[end]
    return OutcomeCheck(
        condition=condition,
        status="uncertain",
        evidence=evidence,
        reason=f"the step was sent once: {look_again} before sending it again",
    )


def already_shown(what: str) -> str:
    """Why a step that expects to see ``what`` is not sent while that shows already: it could not tell what the step
    did."""
    return (
        f"{what} shows already, before the step, so it could not tell what the step did: nothing was sent. Expect "
        "words or an address only the result shows, or leave the expectation out"
    )


def url_shows(url: str, part: str) -> bool:
    """Whether the address ``url`` holds ``part`` in its host and path, or anywhere when ``part`` itself names a query
    or a fragment: ``/checkout`` is not shown by ``/login?next=/checkout``."""
    if "?" in part or "#" in part:
        return part in url
    parts = urlsplit(url)
    return part in urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


# What a browser step expects to see after it (item 1145), which a model writes beside the steps of the call as often
# as on the step (two attempts of fifteen of the speed set on 2026-11-13, each a round trip to write the call again).
# A desktop step takes the words alone.
BROWSER_EXPECTATIONS: tuple[str, ...] = ("expect_text", "expect_url")
DESKTOP_EXPECTATIONS: tuple[str, ...] = ("expect_text",)


def expectation_on_last_step(data: object, expectations: tuple[str, ...]) -> object:
    """An act call (``data``, as the model wrote it) whose fields of ``expectations`` (what its steps expect to see)
    stand beside its steps, where the call has no such field, with them moved onto its last step (item 1161): the plain
    meaning of words the page is to show after the batch, as the last step's are. Left as written when the last step
    has its own, so that the call is refused for the field it does not take, as before: two expectations are not
    merged."""
    if not isinstance(data, dict):
        return data
    steps: object = data.get("steps")
    if not isinstance(steps, list) or not steps or not isinstance(steps[-1], dict):
        return data
    moved: dict[str, object] = {key: data[key] for key in expectations if key in data and key not in steps[-1]}
    if not moved or any(key in steps[-1] for key in expectations if key in data):
        return data
    rest: dict[str, object] = {key: value for key, value in data.items() if key not in moved}
    return {**rest, "steps": [*steps[:-1], {**steps[-1], **moved}]}


def echoes_written(expected: str, written: Sequence[str]) -> bool:
    """Whether the text a step expects to see is within a text the same batch writes: a page that repeats what was
    typed (a search box's "results for …", an error that quotes it) would show it whatever the step did."""
    wanted: str = " ".join(expected.casefold().split())
    return any(wanted in " ".join(text.casefold().split()) for text in written)


def holds_back(checks: Sequence[OutcomeCheck]) -> bool:
    """Whether a step that acts on what the steps before it left must wait: one of their checks is not verified."""
    return any(check.status != "verified" for check in checks)


def _mark(check: OutcomeCheck) -> str:
    said: str = f"{check.condition}; {check.evidence}" if check.evidence else check.condition
    return f" [{check.status}: {said}{f' — {check.reason}' if check.reason else ''}]"


def render_step(report: StepReport, detail: str) -> str:
    """The line of a step in a batch's result: its outcome as before, whether a failed step went out, ``detail`` (its
    own, cut to fit), then each check; a step that is done and was checked for nothing says so, since done is not
    verified."""
    sent: str = _SENT[report.dispatched] if report.outcome == "failed" else ""
    marks: str = "".join(_mark(check) for check in report.checks)
    if not report.checks and report.outcome == "done":
        marks = _NOT_CHECKED
    return f"  {report.index}. {report.description} — {report.outcome}{sent}{f': {detail}' if detail else ''}{marks}"


def render_outcomes(reports: Sequence[StepReport]) -> str:
    """What the checks of a batch's steps came to, for the line above its result: how many verified, contradicted and
    uncertain, and how many steps went through with nothing checked."""
    statuses: list[CheckStatus] = [check.status for report in reports for check in report.checks]
    unchecked: int = sum(1 for report in reports if report.outcome == "done" and not report.checks)
    counted: list[tuple[int, str]] = [
        (statuses.count("verified"), "verified"),
        (statuses.count("contradicted"), "contradicted"),
        (statuses.count("uncertain"), "uncertain"),
        (unchecked, "not checked"),
    ]
    said: str = ", ".join(f"{count} {name}" for count, name in counted if count > 0)
    return f"outcomes: {said}" if said else "outcomes: none"
