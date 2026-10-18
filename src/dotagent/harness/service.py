"""The versions of the harness in the store, and what a run takes from the adopted one (item 389, docs/harness.md): a
version is written (by the owner, later by the improvement loop), adopted, which retires the one adopted before, or
retired; adopting an earlier version again takes a change back."""

import difflib
from collections.abc import Sequence

from pydantic import JsonValue

from dotagent.config import HarnessConfig
from dotagent.core.errors import InvalidValueError, NotFoundError
from dotagent.core.logs import get_logger
from dotagent.harness.components import (
    COMPONENTS,
    NO_HARNESS,
    Component,
    HarnessText,
    component_problem,
    normalized,
    unprintable,
)
from dotagent.store.db import Store
from dotagent.store.models import HarnessVersion

log = get_logger(__name__)
# A version's summary: one line of at most this many characters (what the API's HarnessRequest bounds it to).
SUMMARY_CHARS: int = 200

# Who made a version: the owner, through the command line or the API.
OWNER: str = "owner"


def text_of(version: HarnessVersion) -> HarnessText:
    return HarnessText(habits=version.habits, tool_guidance=version.tool_guidance)


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
    not one line of 1 to :data:`SUMMARY_CHARS` characters or has a character that does not print."""
    clean: HarnessText = HarnessText(habits=normalized(text.habits), tool_guidance=normalized(text.tool_guidance))
    found: dict[str, str] = problems(clean, config, secrets)
    if found:
        details: dict[str, JsonValue] = {name: problem for name, problem in found.items()}
        raise InvalidValueError("the text cannot be a component of the harness", {"problems": details})
    line: str = summary.strip()
    if line == "" or len(line) > SUMMARY_CHARS or "\n" in line or "\t" in line:
        raise InvalidValueError(f"the summary is one line of 1 to {SUMMARY_CHARS} characters", {"length": len(line)})
    hidden: str | None = unprintable(line)
    if hidden is not None:
        raise InvalidValueError("the summary has a character that does not print", {"character": hidden})
    parent: str | None = await store.adopted_harness_id()
    version: HarnessVersion = await store.create_harness_version(
        parent, made_by, line, clean.habits, clean.tool_guidance, notes
    )
    log.info("harness_version_written", version=version.id, made_by=made_by, parent=parent)
    return version


async def adopt(store: Store, version_id: str, now: float) -> HarnessVersion:
    """Make ``version_id`` the version every run takes from now on; the one adopted before is retired."""
    version: HarnessVersion = await store.adopt_harness(version_id, now)
    log.info("harness_adopted", version=version.id)
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


async def adopted_text(store: Store) -> tuple[HarnessText, str | None]:
    """What a run starting now takes from the harness, and the id of the version it is, ``None`` with none adopted."""
    version: HarnessVersion | None = await adopted(store)
    return (NO_HARNESS, None) if version is None else (text_of(version), version.id)


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
