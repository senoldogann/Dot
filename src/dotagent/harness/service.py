"""The versions of the harness in the store, and what a run takes from the adopted one (item 389, docs/harness.md): a
version is written (by the owner, later by the improvement loop), adopted, which retires the one adopted before, or
retired; adopting an earlier version again takes a change back."""

import difflib
import json
from collections.abc import Iterator, Sequence

from pydantic import JsonValue

from dotagent.config import HarnessConfig
from dotagent.core.errors import InvalidValueError, NotFoundError
from dotagent.core.learned_text import gate
from dotagent.core.logs import get_logger
from dotagent.harness.components import (
    COMPONENTS,
    NO_HARNESS,
    Component,
    HarnessText,
    component_problem,
    description_problem,
    normalized,
)
from dotagent.store.db import Store
from dotagent.store.models import HarnessStep, HarnessVersion

log = get_logger(__name__)
# A version's summary: one line of at most this many characters (what the API's HarnessRequest bounds it to).
SUMMARY_CHARS: int = 200

# Who made a version: the owner, through the command line or the API.
OWNER: str = "owner"


def _strings(value: JsonValue) -> Iterator[str]:
    """Every text in a JSON value, at any depth."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)


def _notes_texts(notes: str) -> tuple[str, ...]:
    """The texts a version's ``notes`` hold: every string of the JSON they are, or the notes as one text when they are
    not JSON."""
    try:
        return tuple(_strings(json.loads(notes)))
    except ValueError:
        return (notes,)


def text_of(version: HarnessVersion) -> HarnessText:
    return HarnessText(habits=version.habits, tool_guidance=version.tool_guidance)


def refusals(text: HarnessText, secrets: Sequence[str] = ()) -> dict[str, str]:
    """What the one gate for learned texts says of each component of ``text`` as it stands now, by its name; empty when
    it refuses none (item 587, review round 1). The gate is asked of a version when it is written, and again whenever
    its text is used: a version written before the gate was as strict (or by a hand that never asked it) is not
    adopted, and a prompt never takes a component it refuses. The limits of size are not asked again: a limit the owner
    lowered since is for what is written from then on."""
    return {name: problem for name in COMPONENTS if (problem := gate(text.component(name), secrets)) is not None}


def refusal_words(refused: dict[str, str]) -> str:
    """``refused`` (:func:`refusals`) as a phrase: ``its habits hold a link; its tool guidance holds a secret``."""
    return "; ".join(
        f"its {name.replace('_', ' ')} {'hold' if name == 'habits' else 'holds'} {problem}"
        for name, problem in refused.items()
    )


def check_adoptable(version: HarnessVersion, secrets: Sequence[str] = ()) -> None:
    """Raise :class:`InvalidValueError` unless the gate for learned texts accepts both texts of ``version`` as they
    stand: what a run follows is never a text that the gate would refuse to write, however the version came to be
    (item 587, review round 1). The message says how to set the version aside."""
    refused: dict[str, str] = refusals(text_of(version), secrets)
    if refused:
        raise InvalidValueError(
            f"{version.id} cannot be adopted: {refusal_words(refused)}. Set it aside "
            f"(dotx harness retire {version.id}, or Set aside on the Harness page) and write a new version.",
            {"version": version.id, "problems": {name: problem for name, problem in refused.items()}},
        )


def problems(text: HarnessText, config: HarnessConfig, secrets: Sequence[str]) -> dict[str, str]:
    """What is wrong with each component of ``text`` (:func:`component_problem`), by its name; empty when nothing is."""
    limits: dict[Component, int] = {"habits": config.habits_chars, "tool_guidance": config.tool_guidance_chars}
    found: dict[str, str] = {}
    for name in COMPONENTS:
        problem: str | None = component_problem(text.component(name), limits[name], config.component_lines, secrets)
        if problem is not None:
            found[name] = problem
    return found


async def create_version(
    store: Store,
    config: HarnessConfig,
    text: HarnessText,
    summary: str,
    made_by: str,
    notes: str,
    secrets: Sequence[str],
) -> HarnessVersion:
    """A new candidate from ``text``, normalized (:func:`normalized`), made from the adopted version. A component that
    cannot be one (:func:`problems`) raises :class:`InvalidValueError` naming each problem, as does a summary that is
    not one line of 1 to :data:`SUMMARY_CHARS` characters, and a summary or a text in the ``notes`` (the moments and the
    decisions a model wrote) that the gate refuses (:func:`description_problem`, item 587): whoever the maker, nothing a
    model wrote into the harness is stored unread."""
    clean: HarnessText = HarnessText(habits=normalized(text.habits), tool_guidance=normalized(text.tool_guidance))
    found: dict[str, str] = problems(clean, config, secrets)
    if found:
        details: dict[str, JsonValue] = {name: problem for name, problem in found.items()}
        raise InvalidValueError("the text cannot be a component of the harness", {"problems": details})
    line: str = summary.strip()
    if line == "" or len(line) > SUMMARY_CHARS or "\n" in line or "\t" in line:
        raise InvalidValueError(f"the summary is one line of 1 to {SUMMARY_CHARS} characters", {"length": len(line)})
    for what, written in (("summary", line), *(("notes", each) for each in _notes_texts(notes))):
        refused: str | None = description_problem(written, secrets)
        if refused is not None:
            raise InvalidValueError(f"the {what} cannot be recorded: {refused}", {"problem": refused})
    parent: str | None = await store.adopted_harness_id()
    version: HarnessVersion = await store.create_harness_version(
        parent, made_by, line, clean.habits, clean.tool_guidance, notes
    )
    log.info("harness_version_written", version=version.id, made_by=made_by, parent=parent)
    return version


async def adopt(store: Store, version_id: str, now: float, secrets: Sequence[str] = ()) -> HarnessVersion:
    """Make ``version_id`` the version every run takes from now on; the one adopted before is retired. A version whose
    text the gate for learned texts refuses now (:func:`check_adoptable`; ``secrets`` are the values Dot holds) is not
    adopted, and raises :class:`InvalidValueError` saying why."""
    check_adoptable(await store.get_harness_version(version_id), secrets)
    version: HarnessVersion = await store.adopt_harness(version_id, now)
    log.info("harness_adopted", version=version.id)
    return version


async def adopt_replacing(
    store: Store, version_id: str, replacing: str | None, now: float, secrets: Sequence[str] = ()
) -> HarnessVersion | None:
    """Make ``version_id`` the version every run takes, but only if the version in use is still ``replacing`` (``None``:
    none) and ``version_id`` is still a candidate or a probed one (item 588): what the improvement loop does, since it
    compared the version with the one in use when its step began and never replaces what the owner chose or set aside
    since, and never one whose text the gate for learned texts refuses now (:func:`check_adoptable`; ``secrets`` are
    the values Dot holds). ``None`` when it did not adopt."""
    try:
        check_adoptable(await store.get_harness_version(version_id), secrets)
    except InvalidValueError:
        log.warning("harness_adopt_refused", version=version_id, replacing=replacing)
        return None
    version: HarnessVersion | None = await store.adopt_harness_replacing(version_id, replacing, now)
    if version is None:
        log.info("harness_adopt_skipped", version=version_id, replacing=replacing)
    else:
        log.info("harness_adopted", version=version.id, replacing=replacing)
    return version


async def retire(store: Store, version_id: str, now: float) -> HarnessVersion:
    """Put ``version_id`` aside; when it was adopted, a run's prompt is Dot's own text alone from now on."""
    version: HarnessVersion = await store.retire_harness(version_id, now)
    log.info("harness_retired", version=version.id)
    return version


async def adopted(store: Store) -> HarnessVersion | None:
    """The adopted version, or ``None`` while a run takes Dot's own text alone (also when the record the store's key
    names is gone or no longer adopted, which is logged)."""
    version_id: str | None = await store.adopted_harness_id()
    if version_id is None:
        return None
    try:
        version: HarnessVersion = await store.get_harness_version(version_id)
    except NotFoundError:
        log.warning("harness_adopted_missing", version=version_id)
        return None
    if version.state != "adopted":
        log.warning("harness_adopted_not_adopted", version=version_id, state=version.state)
        return None
    return version


# The components of the adopted version that were left out of a prompt, which are logged once each, not at every run.
_LEFT_OUT: set[tuple[str, str]] = set()


async def adopted_text(store: Store, secrets: Sequence[str] = ()) -> tuple[HarnessText, str | None]:
    """What a run starting now takes from the harness, and the id of the version it is, ``None`` with none adopted. A
    component that the gate for learned texts refuses now (:func:`refusals`: a version written before the gate was as
    strict, or by a hand that never asked it; ``secrets`` are the values Dot holds, which a caller reads as the
    write-time check does) is left out of the text, and logged once; the Harness page shows the version as refused
    (item 587)."""
    version: HarnessVersion | None = await adopted(store)
    if version is None:
        return NO_HARNESS, None
    text: HarnessText = text_of(version)
    refused: dict[str, str] = refusals(text, secrets)
    for name, problem in refused.items():
        if (version.id, name) not in _LEFT_OUT:
            _LEFT_OUT.add((version.id, name))
            log.warning("harness_component_left_out", version=version.id, component=name, problem=problem)
    if refused:
        text = HarnessText(
            habits="" if "habits" in refused else text.habits,
            tool_guidance="" if "tool_guidance" in refused else text.tool_guidance,
        )
    return text, version.id


def diff_text(before: HarnessText, after: HarnessText, before_name: str, after_name: str) -> str:
    """The unified diff of each component from ``before`` to ``after``, named by the versions' names; empty when the
    texts are the same."""
    parts: list[str] = []
    for name in COMPONENTS:
        lines: list[str] = list(
            difflib.unified_diff(
                before.component(name).splitlines(),
                after.component(name).splitlines(),
                fromfile=f"{before_name}/{name}",
                tofile=f"{after_name}/{name}",
                lineterm="",
            )
        )
        if lines:
            parts.append("\n".join(lines))
    return "\n".join(parts)


def _parsed_notes(notes: str, where: dict[str, JsonValue]) -> dict[str, JsonValue]:
    if notes == "":
        return {}
    try:
        parsed: object = json.loads(notes)
    except json.JSONDecodeError as exc:
        raise InvalidValueError("the notes are not JSON", where) from exc
    if not isinstance(parsed, dict):
        raise InvalidValueError("the notes are not a JSON object", where)
    return parsed


def notes_of(version: HarnessVersion) -> dict[str, JsonValue]:
    """A version's notes as the JSON object Dot writes them as (``{}`` for none: a version of the owner's). Notes that
    are not one are a fault, raised as :class:`InvalidValueError`, never read around."""
    return _parsed_notes(version.notes, {"version": version.id})


# What a run read, in words, by the kind or the channel that marks a run as one that read content third parties can
# author (:data:`dotagent.policy.types.TAINTING_KINDS` and ``OUTSIDE_CHANNELS``; ``tainted`` is a result made while the
# run was tainted already): what a version written from it says of where its text may come from (item 588).
READ_AS: dict[str, str] = {
    "network": "a web address",
    "browser_read": "a page in the browser",
    "browser_navigate": "a page in the browser",
    "desktop_observe": "what the screen showed",
    "mcp": "an answer of a tool server",
    "mcp_read": "an answer of a tool server",
    "web": "a web page",
    "browser": "a page in the browser",
    "tainted": "content it had read before",
}
_READ_UNKNOWN: str = "content others can author (which, was not recorded)"


async def taint_of(store: Store, version: HarnessVersion, seen: frozenset[str] = frozenset()) -> tuple[str, ...]:
    """What tainted ``version``, in words, none when nothing did (item 588, review round 1): the run it was written
    from read content third parties can author (its notes say so, ``tainted``, and what, ``tainted_by``), or a version
    it was composed of (``composed_of``) was written from such a run, whatever its own notes say: a version composed
    before they carried the taint is as tainted as its parts. A version whose notes cannot be read, or whose part is
    gone, is never shown clean: that is a taint too, and says so. ``seen`` are the versions on the way here."""
    if version.id in seen:
        return ()
    try:
        notes: dict[str, JsonValue] = notes_of(version)
    except InvalidValueError:
        return ("its notes cannot be read",)
    reasons: list[str] = []
    if notes.get("tainted") is True:
        by: JsonValue = notes.get("tainted_by")
        read: list[str] = (
            sorted({READ_AS.get(item, item) for item in by if isinstance(item, str)}) if isinstance(by, list) else []
        )
        reasons.extend(f"its run read {words}" for words in read or [_READ_UNKNOWN])
    parts: JsonValue = notes.get("composed_of")
    for part_id in parts if isinstance(parts, list) else []:
        if not isinstance(part_id, str):
            continue
        try:
            part: HarnessVersion = await store.get_harness_version(part_id)
        except NotFoundError:
            reasons.append(f"a version it was composed of ({part_id}) is gone")
            continue
        # A reason says in which version the run read it: the nearest one to where it happened.
        reasons.extend(
            reason if "(in " in reason else f"{reason} (in {part_id})"
            for reason in await taint_of(store, part, seen | {version.id})
        )
    return tuple(dict.fromkeys(reasons))


async def is_tainted(store: Store, version: HarnessVersion) -> bool:
    """Whether :func:`taint_of` finds anything that tainted ``version``: its text may come from content third parties
    can author."""
    return bool(await taint_of(store, version))


def notes_with(notes: str, more: dict[str, JsonValue]) -> str:
    """A version's ``notes`` (JSON, or none) with ``more`` added."""
    return json.dumps({**_parsed_notes(notes, {"notes": notes[:80]}), **more}, ensure_ascii=False)


def step_facts(step: HarnessStep) -> dict[str, JsonValue]:
    """What a step did, as the JSON object :func:`dotagent.harness.step.run_step` writes (``{}`` while it runs);
    anything else is a fault, raised as :class:`InvalidValueError`, never read around."""
    try:
        facts: object = json.loads(step.outcome)
    except json.JSONDecodeError as exc:
        raise InvalidValueError("a step's outcome is not JSON", {"step": step.id}) from exc
    if not isinstance(facts, dict):
        raise InvalidValueError("a step's outcome is not a JSON object", {"step": step.id})
    return facts
