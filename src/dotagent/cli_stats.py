"""``dotx stats``: numbers about the runs that really happened, per model: how they ended, what they cost, how often a
tool call failed and where the time went; and per tool, what its calls cost. The database is only read."""

import asyncio
import sys
import time
from collections.abc import Sequence
from datetime import datetime
from typing import Literal

from dotagent.agent.context_parts import ContextParts, tokens_of
from dotagent.agent.learning_health import (
    LEARNING_HEALTH_KEY,
    LEARNING_WORKS,
    WORK_NAMES,
    LearningHealth,
    parse_health,
    quiet_limit,
    stall_reason,
    work_health,
)
from dotagent.agent.reflection_record import (
    REFLECTION_FAILED_KEY,
    REFLECTION_KEY,
    Reflection,
    ReflectionFailure,
    summary_line,
)
from dotagent.cli_commands import Command, StatsCommand
from dotagent.config import load_user_config
from dotagent.core.errors import InvalidValueError
from dotagent.core.paths import db_path
from dotagent.harness.schedule import STEP_OFF, STEP_TIME_KEY, schedule_from
from dotagent.scheduler.failures import SCHEDULE_FAILURES_KEY, FiringFailure, FiringFailures, parse_failures
from dotagent.store.endings import ENDING_LABELS
from dotagent.store.stats import (
    ActStats,
    ChannelStats,
    CompactionStats,
    DayOutcomes,
    HarnessUse,
    LearningStats,
    ModelStats,
    RunCosts,
    RunStatistics,
    SearchCount,
    SiteStats,
    StepBand,
    StuckWork,
    TimeStats,
    ToolStats,
    WebFailures,
    collect_run_costs,
    collect_statistics,
    collect_stuck,
    gui_share,
    read_context_parts,
    read_kv,
)

_DAY_S: float = 86400.0
# What a run that took no version of the harness took: Dot's own text alone.
OWN_TEXT: str = "Dot's own text"
# The tools listed: the ones whose calls took the most seconds in all.
_TOOLS_SHOWN: int = 15


def _count(count: int) -> str:
    return (
        f"{count / 1_000_000:.1f} M"
        if count >= 1_000_000
        else f"{count / 1000:.1f} k"
        if count >= 10_000
        else str(count)
    )


def _percent(part: int, whole: int) -> str:
    return "-" if whole == 0 else f"{round(100 * part / whole)} %"


def _per(total: int, count: int) -> str:
    return "-" if count == 0 else _count(round(total / count))


def render_model(stats: ModelStats) -> list[str]:
    endings: str = ", ".join(f"{ENDING_LABELS[entry.ending]} {entry.runs}" for entry in stats.endings)
    return [
        f"{stats.model}: {stats.runs} runs",
        f"  how they ended: {endings}",
        f"  steps {stats.steps} ({_per(stats.steps, stats.runs)} a run), input tokens {_count(stats.input_tokens)} "
        f"({_per(stats.input_tokens, stats.steps)} a step), output tokens {_count(stats.output_tokens)}",
        f"  tools: {stats.tool_calls} calls, {stats.tool_errors} failed "
        f"({_percent(stats.tool_errors, stats.tool_calls)}), "
        f"{stats.invalid_arguments} with arguments that were not valid JSON, {stats.rejected_arguments} refused for "
        "arguments that did not fit (a turn lost each)",
        f"  stalls {stats.stalls}, compactions {_compaction(stats.compaction)}",
        f"  web failures: {_web(stats.web)}",
        f"  searches answered: {_searches(stats.searches)}",
        f"  delegated runs {stats.delegation.runs}, waited {_seconds(stats.delegation.seconds)} s for them",
        f"  acts: {_acts(stats.acts)}",
        f"  step model call and input tokens by step number: {_bands(stats.bands)}",
    ]


def _compaction(compaction: CompactionStats) -> str:
    """The compactions in one line: how many, per 100 steps, their seconds and their share of the model time."""
    per: str = "-" if compaction.per_100_steps is None else f"{compaction.per_100_steps:.1f}"
    return (
        f"{compaction.compactions} ({per} per 100 steps, {_seconds(compaction.seconds)} s, "
        f"{_share(compaction.share)} of the model time)"
    )


def _searches(searches: Sequence[SearchCount]) -> str:
    return "none" if not searches else ", ".join(f"{entry.engine} {entry.searches}" for entry in searches)


def _web(web: WebFailures) -> str:
    return (
        f"not found {web.not_found}, throttled {web.throttled}, blocked {web.blocked}, in transit {web.transit}, "
        f"other {web.other}"
    )


def _seconds(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def _share(value: float | None) -> str:
    return "-" if value is None else f"{round(100 * value)} %"


def _acts(acts: ActStats) -> str:
    """The act calls in one line: how many, the single-action share, the failed share and the median call."""
    if acts.acts == 0:
        return "none counted"
    return (
        f"{acts.acts} calls, {_percent(acts.single, acts.acts)} with one step, {_percent(acts.failed, acts.acts)} "
        f"failed, median {_seconds(acts.seconds_median)} s"
    )


def _bands(bands: Sequence[StepBand]) -> str:
    """The slowdown curve in one line: the median seconds of a step's model call and, when the steps recorded them,
    the median input tokens it sent, per band of step numbers."""
    if not bands:
        return "no timed steps"
    return ", ".join(
        f"{band.first}-{band.last if band.last else ''} {_seconds(band.seconds_median)} s"
        f"{'' if band.tokens_median is None else f' / {_count(band.tokens_median)} tokens'} ({band.steps})"
        for band in bands
    )


def render_time(stats: Sequence[ModelStats]) -> list[str]:
    """The time table: per model, where the seconds of its runs went, from the model calls they recorded. A model none
    of whose runs recorded a call has no row; the note under the table counts its runs."""
    width: int = max(len("model"), *(len(entry.model) for entry in stats))
    header: str = (
        f"  {'model':<{width}}  {'timed runs':>10}  {'model s':>9}  {'step median s':>13}  {'step p90 s':>10}  "
        f"{'compaction s':>12}  {'side calls s':>12}  {'cached tokens':>13}  {'cached':>6}"
    )
    timed: list[ModelStats] = [entry for entry in stats if entry.time.timed_runs > 0]
    rows: list[str] = []
    for entry in timed:
        time: TimeStats = entry.time
        rows.append(
            f"  {entry.model:<{width}}  {f'{time.timed_runs} of {entry.runs}':>10}  {_seconds(time.model_seconds):>9}  "
            f"{_seconds(time.step_seconds_median):>13}  {_seconds(time.step_seconds_p90):>10}  "
            f"{_seconds(time.compaction_seconds):>12}  {_seconds(time.side_call_seconds):>12}  "
            f"{_count(time.cached_tokens):>13}  {_share(time.cached_share):>6}"
        )
    untimed: int = sum(entry.runs - entry.time.timed_runs for entry in stats)
    note: list[str] = (
        [f"  {untimed} runs recorded no model calls (most ran before calls were timed) and are left out of this table."]
        if untimed
        else []
    )
    return ["Time, from the model calls the runs recorded (side calls: every call but a step):", header, *rows, *note]


def render_tools(tools: Sequence[ToolStats]) -> list[str]:
    """The tools table: the tools whose calls took the most seconds, with the part spent waiting at the gate."""
    shown: Sequence[ToolStats] = tools[:_TOOLS_SHOWN]
    width: int = max((len("tool"), *(len(entry.tool) for entry in shown)))
    header: str = (
        f"  {'tool':<{width}}  {'calls':>6}  {'failed':>6}  {'seconds':>9}  {'median s':>8}  {'at the gate s':>13}"
    )
    rows: list[str] = [
        f"  {entry.tool:<{width}}  {entry.calls:>6}  {entry.failed:>6}  {_seconds(entry.seconds_total):>9}  "
        f"{_seconds(entry.seconds_median):>8}  {_seconds(entry.waited_s):>13}"
        for entry in shown
    ]
    return [
        f"Tools, the {len(shown)} with the most seconds (at the gate: waiting for an approval or a review):",
        header,
        *rows,
    ]


def render_tool_health(tools: Sequence[ToolStats]) -> list[str]:
    """Every tool by failure rate: one failure in twenty is healthy, one in three is not."""
    if not tools:
        return ["Tool health: no tool call recorded."]
    ordered: list[ToolStats] = sorted(
        tools,
        key=lambda entry: (
            -(entry.failed / entry.calls if entry.calls else 0.0),
            -entry.failed,
            -entry.calls,
            entry.tool,
        ),
    )
    width: int = max(len("tool"), *(len(entry.tool) for entry in ordered))
    header: str = f"  {'tool':<{width}}  {'calls':>6}  {'failed':>6}  {'fail %':>7}  {'median s':>8}"
    rows: list[str] = [
        f"  {entry.tool:<{width}}  {entry.calls:>6}  {entry.failed:>6}  "
        f"{_percent(entry.failed, entry.calls):>7}  {_seconds(entry.seconds_median):>8}"
        for entry in ordered
    ]
    return ["Tool health, the highest failure rate first (failed / calls):", header, *rows]


def render_channels(channels: Sequence[ChannelStats]) -> list[str]:
    """The channels table: how many calls went through a command, an API, the web, the browser, the desktop or the
    run's own tools, and the share of the acting calls that went through a GUI (the cheapest channel first, item 27)."""
    if not channels:
        return ["Channels: no tool call recorded."]
    width: int = max(len("channel"), *(len(entry.channel) for entry in channels))
    header: str = f"  {'channel':<{width}}  {'calls':>6}  {'failed':>6}  {'seconds':>9}"
    rows: list[str] = [
        f"  {entry.channel:<{width}}  {entry.calls:>6}  {entry.failed:>6}  {_seconds(entry.seconds_total):>9}"
        for entry in channels
    ]
    share: float | None = gui_share(channels)
    gui: str = "no acting call" if share is None else f"{share * 100:.0f}% of the acting calls went through a GUI"
    return [f"Channels (the browser and the desktop are the GUI; {gui}):", header, *rows]


def render_sites(sites: Sequence[SiteStats]) -> list[str]:
    """The sites table: per page host or application, what the act calls on it did and how the runs that made them
    ended, the busiest first."""
    if not sites:
        return ["Sites: no act call named a site (the browser and desktop act calls record one since item 22)."]
    width: int = max(len("site"), *(len(entry.site) for entry in sites))
    header: str = (
        f"  {'site':<{width}}  {'acts':>6}  {'one step':>8}  {'failed':>6}  {'median s':>8}  {'runs':>5}  {'done':>5}"
    )
    rows: list[str] = [
        f"  {entry.site:<{width}}  {entry.acts.acts:>6}  {_percent(entry.acts.single, entry.acts.acts):>8}  "
        f"{_percent(entry.acts.failed, entry.acts.acts):>6}  {_seconds(entry.acts.seconds_median):>8}  "
        f"{entry.outcomes.runs:>5}  {_percent(entry.outcomes.done, entry.outcomes.runs):>5}"
        for entry in sites
    ]
    return [
        "Sites, from the browser and desktop act calls (one step: a round trip spent on a single action; runs: the "
        "finished runs that acted there, done: the share of them that ended done):",
        header,
        *rows,
    ]


def render_days(days: Sequence[DayOutcomes]) -> list[str]:
    """The trend of the browser and desktop work (item 308): per day, the finished runs that acted on a site and the
    share of them that ended done."""
    if not days:
        return ["By day: no finished run acted on a site."]
    return [
        "By day, the finished runs that acted on a site (the day each started):",
        *(f"  {entry.day}  {entry.runs:>4} runs  {_percent(entry.done, entry.runs):>5} done" for entry in days),
    ]


def render_learning(learning: LearningStats) -> list[str]:
    """What the runs learned and were handed: the lessons, the notes with how the runs handed them ended, the
    playbooks and, per host, the runs that read one against the ones that did not."""
    lines: list[str] = [
        "Learning",
        f"  lessons: {learning.lessons_proposed} proposed, {learning.lessons_saved} saved, "
        f"{learning.lessons_dismissed} dismissed, failed on {learning.lessons_failed} runs",
        f"  notes handed to {learning.runs_handed_notes} runs"
        + (
            "; " + ", ".join(f"{score.memory_id} to {score.handed} ({score.done} done)" for score in learning.notes)
            if learning.notes
            else ""
        ),
        f"  playbooks: {learning.playbooks_written} written, {learning.playbooks_retired} retired, "
        f"{learning.playbooks_failed} failed, read by {learning.runs_reading_playbooks} runs",
    ]
    for comparison in learning.comparisons:
        lines.append(
            f"  {comparison.host}: with its playbook {comparison.read.runs} runs, "
            f"{comparison.read.acts_per_run:.1f} acts and {comparison.read.seconds_per_run:.0f} s a run; "
            f"without {comparison.unread.runs} runs, {comparison.unread.acts_per_run:.1f} acts and "
            f"{comparison.unread.seconds_per_run:.0f} s a run"
        )
    return lines


def render_harness(uses: Sequence[HarnessUse]) -> list[str]:
    """The finished runs by the version of the harness they took (item 588), the busiest first, with the share that
    ended done: what a version did for the runs that had it, against the runs on Dot's own text."""
    if not uses or all(use.version is None for use in uses):
        return ["Harness: no finished run took a version of the harness (Dot's own text alone)."]
    width: int = max((len("version"), *(len(use.version or OWN_TEXT) for use in uses)))
    header: str = f"  {'version':<{width}}  {'runs':>5}  {'done':>5}"
    rows: list[str] = [
        f"  {use.version or OWN_TEXT:<{width}}  {use.runs:>5}  {_percent(use.done, use.runs):>5}" for use in uses
    ]
    return [
        "Harness, the finished runs by the version of the harness they took (done: the share that ended done):",
        header,
        *rows,
    ]


def render_stats(stats: RunStatistics, days: int, by: Literal["model", "site", "tool", "all"]) -> list[str]:
    if not stats.models:
        return [f"No runs in the last {days} days."]
    runs: int = sum(entry.runs for entry in stats.models)
    lines: list[str] = [f"{runs} runs in the last {days} days.", ""]
    if by == "tool":
        return [*lines, *render_tool_health(stats.tools)]
    if by != "site":
        for entry in stats.models:
            lines.extend([*render_model(entry), ""])
        lines.extend(render_time(stats.models))
        if stats.tools:
            lines.extend(["", *render_tools(stats.tools)])
        lines.extend(["", *render_channels(stats.channels)])
    if by != "model":
        lines.extend(["", *render_sites(stats.sites)] if by == "all" else render_sites(stats.sites))
        lines.extend(["", *render_days(stats.days)])
    if by != "site":
        lines.extend(["", *render_learning(stats.learning)])
        lines.extend(["", *render_harness(stats.harness)])
    return lines


def render_run_costs(costs: RunCosts) -> list[str]:
    """What one run cost and where it went: its minutes, its model's numbers, its tools' seconds and its tools."""
    stats: ModelStats = costs.stats
    head: list[str] = [
        f"Run {costs.run_id} ({costs.status}) on {stats.model}: {costs.minutes:.1f} min, {stats.steps} steps, "
        f"input tokens {_count(stats.input_tokens)} ({_per(stats.input_tokens, stats.steps)} a step), "
        f"output tokens {_count(stats.output_tokens)}",
        f"  tool calls took {_seconds(costs.tool_seconds)} s, of which {_seconds(costs.waited_s)} s at the gate",
        f"  harness: {costs.harness_version or OWN_TEXT}",
    ]
    return [*head, *render_model(stats)[3:], "", *render_time([stats]), "", *render_tools(costs.tools)]


async def cmd_stats(command: StatsCommand) -> int:
    if command.run is not None:
        costs: RunCosts = await asyncio.to_thread(collect_run_costs, db_path(), command.run, time.time())
        sys.stdout.write("\n".join(render_run_costs(costs)) + "\n")
        return 0
    since: float = time.time() - command.days * _DAY_S
    stats: RunStatistics = await asyncio.to_thread(collect_statistics, db_path(), since)
    lines: list[str] = render_stats(stats, command.days, command.by)
    if command.by in ("model", "all"):
        last: str | None = await asyncio.to_thread(read_kv, db_path(), REFLECTION_KEY)
        failed: str | None = await asyncio.to_thread(read_kv, db_path(), REFLECTION_FAILED_KEY)
        lines.append(
            render_reflection(
                None if last is None else Reflection.model_validate_json(last),
                None if failed is None else ReflectionFailure.model_validate_json(failed),
            )
        )
        health: str | None = await asyncio.to_thread(read_kv, db_path(), LEARNING_HEALTH_KEY)
        config = await asyncio.to_thread(load_user_config)
        quiet_s: float | None = quiet_limit(config.agent.reflection_time)
        # The page's choice wins over the settings file's (item 721).
        kept: str | None = await asyncio.to_thread(read_kv, db_path(), STEP_TIME_KEY)
        harness_on: bool = schedule_from(kept, config.harness).step_time != STEP_OFF
        lines.extend(render_learning_health(parse_health(health), time.time(), quiet_s, harness_on))
        context: list[str] = await asyncio.to_thread(read_context_parts, db_path(), since, _CONTEXT_RUNS)
        lines.extend(render_context(context))
        stuck: StuckWork = await asyncio.to_thread(collect_stuck, db_path())
        failing: str | None = await asyncio.to_thread(read_kv, db_path(), SCHEDULE_FAILURES_KEY)
        lines.extend(render_stuck(stuck, parse_failures(failing)))
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


# The runs whose first step ``dotx stats`` reads for what it carries (item 735), and how many of its largest parts each
# line names.
_CONTEXT_RUNS: int = 200
_PARTS_NAMED: int = 5


def _median(values: list[int]) -> int:
    ordered: list[int] = sorted(values)
    return ordered[len(ordered) // 2] if ordered else 0


def _largest(parts: list[dict[str, int]], count: int) -> str:
    """The ``count`` largest parts by their median over ``parts``, in tokens; a part missing from a run counts 0."""
    names: set[str] = {name for each in parts for name in each}
    medians: dict[str, int] = {name: _median([each.get(name, 0) for each in parts]) for name in names}
    top: list[tuple[str, int]] = sorted(medians.items(), key=lambda item: -item[1])[:count]
    return ", ".join(f"{name} {tokens_of(chars):,}" for name, chars in top if chars > 0)


def render_context(raw: list[str]) -> list[str]:
    """What the first step of the newest runs carried to their model (item 735), as medians in Dot's estimate of
    tokens: in all, the system prompt and its largest sections, the tools' schemas and their largest groups, and the
    conversation before the first step."""
    if not raw:
        return ["", "What a run's first step carries: nothing recorded yet."]
    parts: list[ContextParts] = [ContextParts.model_validate_json(each) for each in raw]
    system: int = _median([sum(each.system.values()) for each in parts])
    tools: int = _median([sum(each.tools.values()) for each in parts])
    history: int = _median([sum(each.history.values()) for each in parts])
    return [
        "",
        f"What a run's first step carries (median of the newest {len(parts)} runs, estimated tokens):",
        f"  in all {_median([each.tokens for each in parts]):,}",
        f"  system prompt {tokens_of(system):,}: {_largest([each.system for each in parts], _PARTS_NAMED)}",
        f"  tools {tokens_of(tools):,} ({_median([each.tools_offered for each in parts])} offered): "
        f"{_largest([each.tools for each in parts], _PARTS_NAMED)}",
        f"  the conversation {tokens_of(history):,}",
    ]


def _when(at: float) -> str:
    return datetime.fromtimestamp(at).astimezone().strftime("%Y-%m-%d %H:%M")


def render_reflection(last: Reflection | None, failed: ReflectionFailure | None) -> str:
    """The line of the nightly reflection: when it was last done and what it did, and the last one that failed since,
    with what failed."""
    done: str = "never done" if last is None else f"last done {_when(last.at)}: {summary_line(last)}"
    if failed is not None and (last is None or last.at < failed.at):
        return f"  reflection: {done}; failed {_when(failed.at)}: {failed.error}"
    return f"  reflection: {done}"


def render_learning_health(
    health: LearningHealth | None, now: float, quiet_s: float | None, harness_on: bool
) -> list[str]:
    """How learning fares (:mod:`dotagent.agent.learning_health`): when each work last went well, its failures in a
    row since with the last error, and, when learning has stopped, why and whether the owner was told. The harness's
    step (the nightly self-improvement) is "off" while its schedule keeps it from running (``harness_on``: the page's
    time, else ``[harness] step_time``, item 721),
    and says when it last went well if it ever did (item 613: it said it went well days after it was switched off)."""
    if health is None:
        return ["  learning health: nothing recorded yet"]
    lines: list[str] = []
    for work in LEARNING_WORKS:
        state = work_health(health, work)
        if work == "harness" and not harness_on:
            ran: str = "" if state.last_success is None else f"; it last went well {_when(state.last_success)}"
            lines.append(f"  {WORK_NAMES[work].lower()}: off (Settings › Self-improvement){ran}")
            continue
        went: str = "never went well" if state.last_success is None else f"last went well {_when(state.last_success)}"
        failing: str = (
            f"; {state.failures_in_row} failed in a row, the last {_when(state.last_failure)}: {state.last_error}"
            if state.failures_in_row > 0 and state.last_failure is not None
            else ""
        )
        lines.append(f"  {WORK_NAMES[work].lower()}: {went}{failing}")
    reason: str | None = stall_reason(health, now, quiet_s)
    if reason is not None:
        told: str = "" if health.told_at is None else f" (the owner was told {_when(health.told_at)})"
        lines.append(f"  learning has stopped: {reason}{told}")
    return lines


def render_stuck(stuck: StuckWork, failures: FiringFailures) -> list[str]:
    """What waits and will not move on by itself (the code review's O3-O6), counted: the switched-on schedules whose
    firings fail (:mod:`dotagent.scheduler.failures`), each with how many in a row and the last error; the schedules
    marked as running a run none of theirs is at work on; the active campaigns with no session planned; and the chats
    whose queued messages nothing moves on. The chats whose queue waits for the owner are counted apart: they wait for
    a resume, not for a fix."""
    failing: list[tuple[str, FiringFailure]] = [
        (stuck.schedules[schedule_id], failure)
        for schedule_id, failure in failures.schedules.items()
        if schedule_id in stuck.schedules
    ]
    parts: dict[str, int] = {
        "failing schedules": len(failing),
        "schedules marked running with no run at work": stuck.stale_schedules,
        "active campaigns with no session planned": stuck.unplanned_campaigns,
        "chat queues nothing moves on": stuck.stalled_queues,
    }
    said: str = ", ".join(f"{what}: {number}" for what, number in parts.items() if number)
    lines: list[str] = [f"  stuck: {sum(parts.values())}" + (f" ({said})" if said else "")]
    lines.extend(
        f"    {name}: {failure.failures_in_row} firings failed in a row, the last {_when(failure.last_failure_at)}: "
        f"{failure.last_error}"
        for name, failure in failing
    )
    if stuck.held_queues:
        lines.append(f"  chat queues held until you resume them: {stuck.held_queues}")
    return lines


async def dispatch(command: Command) -> int:
    """Carry out one of this module's commands."""
    match command:
        case StatsCommand():
            return await cmd_stats(command)
        case _:
            raise InvalidValueError("not a command of this module", {"name": command.name})
