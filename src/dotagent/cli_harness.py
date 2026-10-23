"""``dotx harness``: the versions of what a run's prompt takes from the harness (item 389, docs/harness.md): list,
show and diff them, write one of the owner's own, adopt or retire one."""

import asyncio
import json
import signal
import sys
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from pathlib import Path

from pydantic import JsonValue

from dotagent.cli_commands import (
    Command,
    HarnessAdoptCommand,
    HarnessDiffCommand,
    HarnessExperiencesCommand,
    HarnessFinalCommand,
    HarnessGraphCommand,
    HarnessListCommand,
    HarnessProbeCommand,
    HarnessProposeCommand,
    HarnessRetireCommand,
    HarnessSetCommand,
    HarnessShowCommand,
    HarnessStepCommand,
    HarnessStepsCommand,
)
from dotagent.config import AppConfig, load_user_config
from dotagent.core.errors import InvalidValueError
from dotagent.core.paths import db_path, screenshots_dir
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedTurn
from dotagent.harness.components import COMPONENTS, NO_HARNESS, HarnessText
from dotagent.harness.contrast import scores_of
from dotagent.harness.graph import Lineage, graph_of, lineage_of, mermaid
from dotagent.harness.probe import ProbeAsk, ProbeReport, ask_from_file, probe_apart, probe_here, report_json
from dotagent.harness.propose import propose_from_run
from dotagent.harness.roles import ROLE_NAMES
from dotagent.harness.service import (
    OWNER,
    adopt,
    adopted,
    create_version,
    diff_text,
    notes_with,
    refusal_words,
    refusals,
    retire,
    step_facts,
    text_of,
)
from dotagent.harness.step import run_step, scripted_role_turns
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.secrets import known_secrets
from dotagent.store.db import Store
from dotagent.store.models import HarnessExperience, HarnessStep, HarnessVersion

# What a version's line names in place of a text: Dot's own text, which a run takes while none is adopted.
OWN_TEXT: str = "Dot's own text"


@asynccontextmanager
async def _store() -> AsyncIterator[Store]:
    """The store alone, opened without the rest of the runtime and closed afterwards."""
    store: Store = await Store.open(db_path(), screenshots_dir())
    try:
        yield store
    finally:
        await store.close()


def _when(ts: float | None) -> str:
    return "" if ts is None else time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def version_line(version: HarnessVersion) -> str:
    """``hrn_… adopted  owner  2026-10-19 03:40  A habit of checking the file first``."""
    return f"{version.id}  {version.state:<9} {version.made_by:<6} {_when(version.created_at)}  {version.summary}"


def render_version(version: HarnessVersion, secrets: Sequence[str] = ()) -> list[str]:
    """The version's line, its parent, what the gate for learned texts refuses of it now (``secrets`` are the values Dot
    holds), then each component under its name, ``(nothing)`` for an empty one."""
    lines: list[str] = [version_line(version), f"made from: {version.parent_id or OWN_TEXT}"]
    refused: dict[str, str] = refusals(text_of(version), secrets)
    if refused:
        lines.append(f"refused: {refusal_words(refused)}; it is not adopted, and no run takes the component")
    for name in COMPONENTS:
        text: str = text_of(version).component(name)
        lines.extend(["", f"## {name}", text if text != "" else "(nothing)"])
    if version.notes != "":
        lines.extend(["", "## notes", version.notes])
    return lines


def _read_component(place: str | None) -> str | None:
    """The text of the file ``place`` names, standard input for ``-``, ``None`` when no file was named."""
    if place is None:
        return None
    if place == "-":
        return sys.stdin.read()
    return Path(place).expanduser().read_text(encoding="utf-8")


async def cmd_harness_list(command: HarnessListCommand) -> int:
    async with _store() as store:
        versions: list[HarnessVersion] = await store.list_harness_versions()
    if not versions:
        sys.stdout.write(f"No version yet: every run takes {OWN_TEXT}. Write one with 'dotx harness set'.\n")
        return 0
    for version in versions:
        sys.stdout.write(f"{version_line(version)}\n")
    return 0


async def cmd_harness_show(command: HarnessShowCommand) -> int:
    async with _store() as store:
        version: HarnessVersion | None = (
            await adopted(store) if command.version_id is None else await store.get_harness_version(command.version_id)
        )
    if version is None:
        sys.stdout.write(f"No version is adopted: every run takes {OWN_TEXT}.\n")
        return 0
    async with _store() as store:
        lineage: Lineage = await lineage_of(store, version.id)
    secrets: tuple[str, ...] = await asyncio.to_thread(known_secrets)
    sys.stdout.write("\n".join([*render_version(version, secrets), *render_lineage(lineage)]) + "\n")
    return 0


def render_lineage(lineage: Lineage) -> list[str]:
    """What the store knows of a version's past: its ancestors, how its probes went, the steps that touched it and the
    experiences about it."""
    lines: list[str] = []
    if lineage.ancestors:
        lines.extend(["", "## made from", *(version_line(older) for older in lineage.ancestors)])
    if lineage.probes:
        passed: int = sum(1 for probe in lineage.probes if probe.passed)
        lines.extend(["", f"## probes: {passed} of {len(lineage.probes)} attempts passed"])
    if lineage.steps:
        lines.extend(
            ["", "## steps", *(f"{step.id}  {step.state}  {_when(step.started_at)}" for step in lineage.steps)]
        )
    if lineage.experiences:
        lines.extend(["", "## experiences", *(experience_line(each) for each in lineage.experiences)])
    return lines


def experience_line(experience: HarnessExperience) -> str:
    when: str = _when(experience.created_at)
    return f"{experience.id}  {when}  [{experience.confidence}] {experience.cue}: {experience.lesson}"


async def cmd_harness_diff(command: HarnessDiffCommand) -> int:
    async with _store() as store:
        version: HarnessVersion = await store.get_harness_version(command.version_id)
        against: HarnessVersion | None
        if command.against is not None:
            against = await store.get_harness_version(command.against)
        else:
            current: HarnessVersion | None = await adopted(store)
            against = None if current is None or current.id == version.id else current
    before: HarnessText = NO_HARNESS if against is None else text_of(against)
    diff: str = diff_text(before, text_of(version), OWN_TEXT if against is None else against.id, version.id)
    sys.stdout.write(f"{diff}\n" if diff != "" else "The texts are the same.\n")
    return 0


async def cmd_harness_set(command: HarnessSetCommand) -> int:
    if command.habits == "-" and command.tool_guidance == "-":
        raise InvalidValueError("standard input can be read for one component only", {})
    habits: str | None = _read_component(command.habits)
    tool_guidance: str | None = _read_component(command.tool_guidance)
    if habits is None and tool_guidance is None:
        raise InvalidValueError("name a file for --habits or --tool-guidance", {})
    config = load_user_config()
    secrets: tuple[str, ...] = await asyncio.to_thread(known_secrets)
    async with _store() as store:
        current: HarnessVersion | None = await adopted(store)
        kept: HarnessText = NO_HARNESS if current is None else text_of(current)
        text: HarnessText = HarnessText(
            habits=kept.habits if habits is None else habits,
            tool_guidance=kept.tool_guidance if tool_guidance is None else tool_guidance,
        )
        version: HarnessVersion = await create_version(store, config.harness, text, command.summary, OWNER, "", secrets)
        if command.adopt:
            version = await adopt(store, version.id, time.time(), secrets)
    sys.stdout.write(f"{'Adopted' if command.adopt else 'Written'}: {version_line(version)}\n")
    if not command.adopt:
        sys.stdout.write(f"Adopt it with 'dotx harness adopt {version.id}'.\n")
    return 0


async def cmd_harness_adopt(command: HarnessAdoptCommand) -> int:
    async with _store() as store:
        version: HarnessVersion = await adopt(store, command.version_id, time.time())
    sys.stdout.write(f"Adopted: {version_line(version)}\nThe runs that start from now on take it.\n")
    return 0


async def cmd_harness_retire(command: HarnessRetireCommand) -> int:
    async with _store() as store:
        version: HarnessVersion = await retire(store, command.version_id, time.time())
        current: HarnessVersion | None = await adopted(store)
    sys.stdout.write(f"Retired: {version_line(version)}\n")
    sys.stdout.write(f"The runs that start from now on take {OWN_TEXT if current is None else current.id}.\n")
    return 0


async def cmd_harness_propose(command: HarnessProposeCommand) -> int:
    """One question to the proposer about a run; its candidate waits for 'dotx harness adopt'."""
    runtime: Runtime = await build_runtime(load_user_config())
    try:
        version: HarnessVersion | None = await propose_from_run(runtime, command.run_id, command.scope)
    finally:
        await close_runtime(runtime)
    if version is None:
        sys.stdout.write(f"The proposer keeps the harness as it is: nothing in {command.run_id} argues for a change.\n")
        return 0
    sys.stdout.write("\n".join(render_version(version)) + "\n")
    sys.stdout.write(
        f"Adopt it with 'dotx harness adopt {version.id}', or see what it changes with 'dotx harness diff'.\n"
    )
    return 0


def render_step(step: HarnessStep) -> list[str]:
    """How a step ended, and what it did."""
    facts: dict[str, object] = json.loads(step.outcome) if step.outcome else {}
    lines: list[str] = [f"Step {step.id}: {step.state}" + (f" ({step.error})" if step.error else "")]
    for key in ("from_run", "candidates", "decision", "probed", "adopted", "reason", "tokens"):
        if key in facts:
            lines.append(f"  {key}: {facts[key]}")
    contrasts: object = facts.get("contrasts")
    if isinstance(contrasts, dict):
        for label, text in contrasts.items():
            lines.append(f"  {label}: {text}")
    return lines


async def cmd_harness_step(command: HarnessStepCommand) -> int:
    """One step now, its probes in processes of their own, as the daemon runs it at [harness] step_time. ``--scripted``
    plays the probes' references through the scripted model and has the roles answer from a script
    (:func:`scripted_role_turns`): the whole step at no model's cost."""
    config: AppConfig = load_user_config()
    if command.scripted:
        config = config.model_copy(
            update={"harness": config.harness.model_copy(update=dict.fromkeys(ROLE_NAMES, SCRIPTED_MODEL))}
        )
    runtime: Runtime = await build_runtime(config)
    try:
        if command.scripted:
            provider: str = parse_model_ref(SCRIPTED_MODEL).provider
            turns: tuple[ScriptedTurn, ...] = scripted_role_turns(len(config.harness.step_candidates))
            await runtime.router.register(provider, ScriptedProvider(provider, turns))
        step: HarnessStep = await run_step(runtime, time.time(), command.run_id, probe_apart, command.scripted)
    finally:
        await close_runtime(runtime)
    sys.stdout.write("\n".join(render_step(step)) + "\n")
    facts: dict[str, JsonValue] = step_facts(step)
    if "probed" in facts:
        sys.stdout.write(
            "The probed version is adopted: the runs that start from now on take it.\n"
            if facts.get("adopted") is True
            else "Adopt the probed version with 'dotx harness adopt ID', or see it with 'dotx harness diff ID'.\n"
        )
    return 0


async def cmd_harness_probe(command: HarnessProbeCommand) -> int:
    """A probe in this process, which runs nothing else: the report goes to standard output as JSON. SIGTERM (the step
    that asked ending, the daemon stopping) cancels the play, so that the attempt under way, its runtime and the
    commands it started end with it."""
    ask: ProbeAsk = ask_from_file(Path(command.ask))
    loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()
    task: asyncio.Task[object] | None = asyncio.current_task()
    if task is not None:
        loop.add_signal_handler(signal.SIGTERM, task.cancel)
    try:
        report: ProbeReport = await probe_here(ask)
    finally:
        if task is not None:
            loop.remove_signal_handler(signal.SIGTERM)
    sys.stdout.write(report_json(report) + "\n")
    return 0


def render_report(report: ProbeReport) -> list[str]:
    scores = scores_of(report)
    lines: list[str] = [
        f"{report.label}: {sum(card.passed for card in report.cards)} of {len(report.cards)} attempts passed, "
        f"{report.tokens} tokens, {report.seconds:.0f} s"
        + (f", cut short by {report.cut_short}" if report.cut_short else "")
    ]
    lines.extend(
        f"  {score.task_id}: {score.passes}/{score.attempts} passed, {score.steps:.1f} steps"
        for score in scores.values()
    )
    return lines


async def cmd_harness_final(command: HarnessFinalCommand) -> int:
    """The final set, once, under the adopted version or the one named; the report is kept in the version's notes
    (`dotx harness show` prints them) and printed as it comes out: what it says is never optimized against."""
    config = load_user_config()
    async with _store() as store:
        version: HarnessVersion | None = (
            await adopted(store) if command.version_id is None else await store.get_harness_version(command.version_id)
        )
        if version is None:
            raise InvalidValueError("no version is adopted; name one to play the final set under", {})
        text: HarnessText = text_of(version)
    ask: ProbeAsk = ProbeAsk(
        label=version.id,
        text=text,
        tasks=tuple(config.harness.final_tasks),
        repeats=1,
        model=config.models.main,
        scripted=False,
        max_minutes=config.harness.step_minutes,
        max_tokens=config.harness.step_tokens,
    )
    report: ProbeReport = await probe_apart(ask)
    async with _store() as store:
        current: HarnessVersion = await store.get_harness_version(version.id)
        await store.set_harness_notes(
            version.id, notes_with(current.notes, {"final": json.loads(report_json(report)), "final_at": time.time()})
        )
    sys.stdout.write("\n".join(render_report(report)) + "\n")
    return 0


async def cmd_harness_graph(command: HarnessGraphCommand) -> int:
    async with _store() as store:
        text: str = mermaid(await graph_of(store, command.steps))
    sys.stdout.write(text + "\n")
    return 0


async def cmd_harness_steps(command: HarnessStepsCommand) -> int:
    async with _store() as store:
        steps: list[HarnessStep] = await store.list_harness_steps(command.limit)
    if not steps:
        sys.stdout.write(
            "No step yet: 'dotx harness step' runs one, [harness] step_time has the daemon run one a night.\n"
        )
        return 0
    for step in steps:
        sys.stdout.write("\n".join(render_step(step)) + "\n")
    return 0


async def cmd_harness_experiences(command: HarnessExperiencesCommand) -> int:
    async with _store() as store:
        experiences: list[HarnessExperience] = await store.list_harness_experiences(command.limit)
    if not experiences:
        sys.stdout.write("No experience yet: a step writes one when an edit helped or hurt with evidence.\n")
        return 0
    for experience in experiences:
        sys.stdout.write(experience_line(experience) + "\n")
    return 0


async def dispatch(command: Command) -> int:
    """Carry out one of this module's commands."""
    match command:
        case HarnessListCommand():
            return await cmd_harness_list(command)
        case HarnessShowCommand():
            return await cmd_harness_show(command)
        case HarnessDiffCommand():
            return await cmd_harness_diff(command)
        case HarnessSetCommand():
            return await cmd_harness_set(command)
        case HarnessAdoptCommand():
            return await cmd_harness_adopt(command)
        case HarnessRetireCommand():
            return await cmd_harness_retire(command)
        case HarnessProposeCommand():
            return await cmd_harness_propose(command)
        case HarnessStepCommand():
            return await cmd_harness_step(command)
        case HarnessProbeCommand():
            return await cmd_harness_probe(command)
        case HarnessFinalCommand():
            return await cmd_harness_final(command)
        case HarnessGraphCommand():
            return await cmd_harness_graph(command)
        case HarnessStepsCommand():
            return await cmd_harness_steps(command)
        case HarnessExperiencesCommand():
            return await cmd_harness_experiences(command)
        case _:
            raise InvalidValueError("not a command of this module", {"name": command.name})
