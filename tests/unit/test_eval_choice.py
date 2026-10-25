"""The choice tasks of the evals (item 659): the checks that read a run's events, the way the audit holds a task to what
it offers, the five tasks of the catalog, and the setting that lets the same tasks be played with the lines of the MCP
servers' groups left bare. The tasks played through the real loop are in ``tests/smoke/test_choice_tasks.py``."""

import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from dotagent.cli import build_parser, parse_command
from dotagent.cli_commands import EvalRunCommand
from dotagent.cli_eval import _report_of, configured_for
from dotagent.config import McpServerConfig, load_default_config
from dotagent.core.types import JsonObject
from dotagent.evals.audit import catalog_problems, task_problems
from dotagent.evals.catalog import CHOICE_TIME_LIMIT_S, CHOICE_TOKEN_BUDGET, all_tasks, select_tasks
from dotagent.evals.checks import describe_check, evaluate_check, evaluate_checks
from dotagent.evals.results import CheckOutcome, EvalReport, EvalSettings
from dotagent.evals.runner import do_nothing_passes, eval_config, with_servers
from dotagent.evals.scoring import render_scorecard, score_report
from dotagent.evals.tool_server import STAND_INS, build_stand_in, stand_in
from dotagent.evals.types import (
    EvalTask,
    GroupStaysOff,
    NoPicks,
    SkillRead,
    SkillUnread,
    ToolRan,
)
from dotagent.store.models import EventRecord

CHOICE_IDS: tuple[str, ...] = (
    "changelog-skill-among-decoys",
    "tweet-skill-among-many",
    "rates-server-by-its-words",
    "timetable-server-by-owners-words",
    "forecast-server-by-its-tools",
)
ENV: dict[str, str] = {"PATH": "/usr/bin:/bin"}


def _event(index: int, kind: str, data: JsonObject) -> EventRecord:
    return EventRecord(id=index, run_id="run-1", ts=float(index), kind=kind, data=data)


def _call(index: int, tool: str, arguments: JsonObject, ok: bool | None) -> list[EventRecord]:
    """The events of a call of ``tool``: the call, and its result when it was answered (``ok`` is None for none)."""
    call_id: str = f"call-{index}"
    events: list[EventRecord] = [_event(index * 10, "tool_call", {"call_id": call_id, "tool": tool, "args": arguments})]
    if ok is not None:
        events.append(_event(index * 10 + 1, "tool_result", {"call_id": call_id, "tool": tool, "ok": ok}))
    return events


def _groups(*on_at_each_step: Sequence[str]) -> list[EventRecord]:
    """The ``tool_groups`` events of a run whose groups on were ``on_at_each_step`` in turn."""
    return [
        _event(index + 1, "tool_groups", {"step": index, "on": list(on), "off": [], "turned_on": [], "reason": ""})
        for index, on in enumerate(on_at_each_step)
    ]


async def _holds(
    check: SkillRead | SkillUnread | ToolRan | GroupStaysOff | NoPicks, events: Sequence[EventRecord]
) -> bool:
    return (await evaluate_check(check, Path("/nonexistent"), "", ENV, events)).passed


# ------------------------------------------------------------------------------------------------ the checks


async def test_a_skill_is_read_when_a_call_of_the_skill_tool_for_it_ran_and_no_other_call_counts() -> None:
    read = SkillRead(kind="skill_read", name="shop-changelog")
    assert await _holds(read, _call(1, "skill", {"name": "shop-changelog"}, True))
    # Another skill, a call that failed, a call nobody answered, another tool with the name in its arguments, nothing.
    assert not await _holds(read, _call(1, "skill", {"name": "release-mail"}, True))
    assert not await _holds(read, _call(1, "skill", {"name": "shop-changelog"}, False))
    assert not await _holds(read, _call(1, "skill", {"name": "shop-changelog"}, None))
    assert not await _holds(read, _call(1, "read_file", {"name": "shop-changelog"}, True))
    assert not await _holds(read, [])
    # One of several is enough.
    assert await _holds(
        read, [*_call(1, "skill", {"name": "release-mail"}, True), *_call(2, "skill", {"name": "shop-changelog"}, True)]
    )
    # What the outcome says when it did not hold: which skills the run did read.
    outcome: CheckOutcome = await evaluate_check(
        read,
        Path("/nonexistent"),
        "",
        ENV,
        [*_call(1, "skill", {"name": "release-mail"}, True), *_call(2, "skill", {"name": "docs-voice"}, True)],
    )
    assert (outcome.check, outcome.passed) == ("the run read the skill shop-changelog", False)
    assert outcome.detail == "the skills it called the skill tool for: release-mail, docs-voice"
    assert (await evaluate_check(read, Path("/nonexistent"), "", ENV, [])).detail.endswith(": none")


async def test_a_skill_is_left_unread_when_the_skill_tool_was_never_called_for_it_whether_or_not_the_call_ran() -> None:
    unread = SkillUnread(kind="skill_unread", name="release-mail")
    assert await _holds(unread, [])
    assert await _holds(unread, _call(1, "skill", {"name": "shop-changelog"}, True))
    assert not await _holds(unread, _call(1, "skill", {"name": "release-mail"}, True))
    assert not await _holds(unread, _call(1, "skill", {"name": "release-mail"}, False))
    assert not await _holds(unread, _call(1, "skill", {"name": "release-mail"}, None))
    assert describe_check(unread) == "the run did not read the skill release-mail"


async def test_a_tool_ran_when_a_call_of_it_was_answered_and_did_not_fail() -> None:
    ran = ToolRan(kind="tool_ran", tool="mcp__ab-tools__lookup")
    assert await _holds(ran, _call(1, "mcp__ab-tools__lookup", {"key": "k"}, True))
    # A call of a tool whose group was off does not run, and a call that failed did not do its work.
    assert not await _holds(ran, _call(1, "mcp__ab-tools__lookup", {"key": "k"}, False))
    assert not await _holds(ran, _call(1, "mcp__ab-tools__lookup", {"key": "k"}, None))
    assert not await _holds(ran, _call(1, "mcp__zq-kit__convert", {"key": "k"}, True))
    assert await _holds(
        ran, [*_call(1, "mcp__ab-tools__lookup", {}, False), *_call(2, "mcp__ab-tools__lookup", {}, True)]
    )
    refused: CheckOutcome = await evaluate_check(
        ran,
        Path("/nonexistent"),
        "",
        ENV,
        [*_call(1, "mcp__ab-tools__lookup", {}, False), *_call(2, "mcp__ab-tools__lookup", {}, False)],
    )
    assert (refused.check, refused.detail) == (
        "the run called mcp__ab-tools__lookup and the call ran",
        "2 calls of it, none of them ran",
    )


async def test_a_group_stays_off_when_no_tool_groups_event_has_it_on() -> None:
    off = GroupStaysOff(kind="group_stays_off", group="mcp__zq-kit")
    assert await _holds(off, _groups(["core"], ["core", "mcp__ab-tools"]))
    assert await _holds(off, [])
    # Turned on by use_tools or by a call of one of its tools: the next event of the groups has it among those on.
    assert not await _holds(off, _groups(["core"], ["core", "mcp__zq-kit"]))
    assert not await _holds(off, _groups(["core", "mcp__zq-kit"]))
    # A group of the same start is another group, and a call of its tool is no turning on by itself.
    assert await _holds(off, [*_groups(["core", "mcp__zq-kit-two"]), *_call(1, "mcp__zq-kit__convert", {}, False)])
    assert describe_check(off) == "the tool group mcp__zq-kit stayed off"


async def test_a_run_had_no_picks_when_no_event_says_the_owner_picked_for_its_message() -> None:
    nothing = NoPicks(kind="no_picks")
    assert await _holds(nothing, [])
    assert await _holds(nothing, _call(1, "skill", {"name": "x"}, True))
    assert not await _holds(
        nothing, [_event(1, "picks", {"plugins": ["ab-tools"], "skills": [], "plan_first": False, "accounts": []})]
    )
    assert describe_check(nothing) == "the message of the run had no picks from the composer"


async def test_the_checks_of_a_task_are_all_judged_with_the_events_of_its_run() -> None:
    checks = (
        SkillRead(kind="skill_read", name="a"),
        SkillUnread(kind="skill_unread", name="b"),
        NoPicks(kind="no_picks"),
    )
    events: list[EventRecord] = _call(1, "skill", {"name": "b"}, True)
    outcomes = await evaluate_checks(checks, Path("/nonexistent"), "", ENV, events)
    assert [outcome.passed for outcome in outcomes] == [False, False, True]
    # Without events (a run that did nothing) none of the checks that ask for something done holds.
    nothing = await evaluate_checks(checks, Path("/nonexistent"), "", ENV)
    assert [outcome.passed for outcome in nothing] == [False, True, True]


# --------------------------------------------------------------------------------------------- the audit


def test_a_check_of_the_events_can_only_name_what_the_task_offers() -> None:
    task: EvalTask = select_tasks(["rates-server-by-its-words"])[0]
    assert task_problems(task) == ()
    # A tool, and a group, of a server the task does not bring.
    ungiven: EvalTask = task.model_copy(update={"servers": task.servers[1:]})
    problems: tuple[str, ...] = task_problems(ungiven)
    assert any(
        "'mcp__ab-tools__lookup', which is not one of an MCP server the task brings" in text for text in problems
    )
    stray: EvalTask = task.model_copy(
        update={"checks": (*task.checks, GroupStaysOff(kind="group_stays_off", group="mcp__nowhere"))}
    )
    assert any("'mcp__nowhere', which is not that of a server the task brings" in text for text in task_problems(stray))
    bare: EvalTask = task.model_copy(
        update={"checks": (*task.checks, GroupStaysOff(kind="group_stays_off", group="zq-kit"))}
    )
    assert any("'zq-kit', which is not that of a server" in text for text in task_problems(bare))
    # A group only exists to turn on when the tools are grouped.
    flat: EvalTask = task.model_copy(update={"grouped": False})
    assert any("the tools of the task are not grouped" in text for text in task_problems(flat))
    # A skill the task does not bring, or one that comes with Dot.
    skilled: EvalTask = select_tasks(["changelog-skill-among-decoys"])[0]
    assert task_problems(skilled) == ()
    assert any(
        "the skill 'shop-changelog', which the task does not bring" in text
        for text in task_problems(skilled.model_copy(update={"skills": skilled.skills[:1]}))
    )
    with_builtin: EvalTask = skilled.model_copy(
        update={"checks": (*skilled.checks, SkillRead(kind="skill_read", name="excel"))}
    )
    assert task_problems(with_builtin) == ()


def test_the_catalog_has_the_five_choice_tasks_and_is_well_formed() -> None:
    assert catalog_problems(all_tasks()) == ()
    tasks: tuple[EvalTask, ...] = select_tasks(CHOICE_IDS)
    assert [task.id for task in tasks] == list(CHOICE_IDS)
    assert {task.category for task in tasks} == {"choice"}
    assert [task.id for task in all_tasks() if task.category == "choice"] == list(CHOICE_IDS)
    for task in tasks:
        # No pick, and the run cut short soon: a run that picks right does it in a few steps.
        assert any(isinstance(check, NoPicks) for check in task.checks), task.id
        assert (task.time_limit_s, task.token_budget) == (CHOICE_TIME_LIMIT_S, CHOICE_TOKEN_BUDGET), task.id
        assert task.easy_way is None and task.injection_checks == (), task.id
        # What a run must have done, to pass, is among the checks of its events.
        assert any(isinstance(check, SkillRead | ToolRan) for check in task.checks), task.id
        assert task.grouped is bool(task.servers), task.id
    # Every other task is played as it was: its tools in the configured surface.
    assert not any(task.grouped for task in all_tasks() if task.category != "choice")


def test_each_choice_task_brings_a_decoy_the_run_must_leave_alone() -> None:
    for task in select_tasks(CHOICE_IDS):
        skills: set[str] = {check.name for check in task.checks if isinstance(check, SkillRead)}
        left: set[str] = {check.name for check in task.checks if isinstance(check, SkillUnread)}
        groups: set[str] = {check.group for check in task.checks if isinstance(check, GroupStaysOff)}
        assert left or groups, task.id
        assert left <= {seed.name for seed in task.skills} - skills, task.id
        # The server the task is for is not the decoy: there is one more server than there are groups to leave off.
        assert len(task.servers) == (len(groups) + 1 if groups else 0), task.id


def test_the_servers_of_the_server_tasks_have_names_that_tell_little_and_say_what_they_are_for_one_way_each() -> None:
    by_name: dict[str, McpServerConfig] = {
        server.name: server for task in select_tasks(CHOICE_IDS) for server in task.servers
    }
    assert sorted(by_name) == ["ab-tools", "kb-desk", "ml-pack", "mv-lab", "pq-dev", "zq-kit"]
    # What the owner wrote: only for the timetable and the freezers.
    assert {name for name, server in by_name.items() if server.description} == {"kb-desk", "mv-lab"}
    # What the server says of itself: its instructions for the bank and the converter, none for the others.
    said: dict[str, str | None] = {one.name: one.instructions for one in STAND_INS}
    assert (
        said["rates"]
        and said["units"]
        and not any(said[name] for name in ("timetable", "freezers", "forecast", "pipelines"))
    )
    for server in by_name.values():
        assert server.command == sys.executable and server.args[:2] == ["-m", "dotagent.evals.tool_server"]
        stand_in(server.args[2])


def test_a_stand_in_answers_from_its_table_and_says_how_a_key_looks_when_it_has_none() -> None:
    rates = stand_in("rates")
    server = build_stand_in(rates)
    assert server.instructions == rates.instructions
    with pytest.raises(ValueError, match="no stand-in server called 'nowhere'; there are rates, units, "):
        stand_in("nowhere")
    quiet = build_stand_in(stand_in("timetable"))
    assert quiet.instructions is None


# ------------------------------------------------------------- the task's own configuration and the A/B switch


def test_a_grouped_task_plays_with_its_tools_in_groups_and_its_servers_given() -> None:
    config = eval_config(load_default_config())
    assert config.agent.tool_surface == "full" and config.agent.mcp_purposes is True
    task: EvalTask = select_tasks(["rates-server-by-its-words"])[0]
    played = with_servers(config, task)
    assert played.agent.tool_surface == "grouped"
    assert [server.name for server in played.mcp] == ["ab-tools", "zq-kit"]
    # The setting of the lines is the run's, not the task's; the rest of the agent's settings are as they were.
    assert played.agent.model_copy(update={"tool_surface": "full"}) == config.agent
    quiet = with_servers(config, select_tasks(["write-todo-note"])[0])
    assert quiet.agent == config.agent and quiet.mcp == []
    off = with_servers(
        config.model_copy(update={"agent": config.agent.model_copy(update={"mcp_purposes": False})}), task
    )
    assert off.agent.mcp_purposes is False and off.agent.tool_surface == "grouped"


async def test_a_run_that_does_nothing_passes_none_of_the_choice_tasks() -> None:
    for task in select_tasks(CHOICE_IDS):
        assert not await do_nothing_passes(task, ENV), task.id


def test_the_switch_that_leaves_the_lines_bare_is_a_flag_of_eval_run_and_the_report_records_it() -> None:
    parser = build_parser()
    played = parse_command(parser, ["eval", "run", "--task", "rates-server-by-its-words", "--repeat", "3"])
    bare = parse_command(parser, ["eval", "run", "--task", "rates-server-by-its-words", "--no-mcp-purposes"])
    assert isinstance(played, EvalRunCommand) and isinstance(bare, EvalRunCommand)
    assert (played.no_mcp_purposes, bare.no_mcp_purposes) == (False, True)
    assert (played.repeat, bare.repeat) == (3, 1)
    # The flag sets the setting for this run and nothing else; without it the configuration is the owner's own.
    config = load_default_config()
    assert config.agent.mcp_purposes is True
    assert configured_for(config, played) == config
    off = configured_for(config, bare)
    assert off.agent.mcp_purposes is False and off.agent.model_copy(update={"mcp_purposes": True}) == config.agent
    assert off.model_copy(update={"agent": config.agent}) == config
    mine = config.model_copy(update={"agent": config.agent.model_copy(update={"mcp_purposes": False})})
    assert configured_for(mine, played).agent.mcp_purposes is False
    # The report says whether the lines were there, so that two reports can be told apart.
    for run, expected in ((config, True), (off, False)):
        settings = _report_of(run, "ollama:test", 0.0, ()).settings
        assert settings.mcp_purposes is expected
    # A report saved before there were lines says they were not there.
    settings = EvalSettings(effort="medium", goal_mode=False, context_tokens=48000, preset="aggressive")
    assert settings.mcp_purposes is False
    for on in (True, False):
        report = EvalReport(
            model="ollama:test",
            started_at=0.0,
            finished_at=1.0,
            settings=settings.model_copy(update={"mcp_purposes": on}),
            outcomes=(),
        )
        assert f"server purposes {'on' if on else 'off'}" in "\n".join(render_scorecard(score_report(report)))
    old = EvalSettings.model_validate(
        {"effort": "medium", "goal_mode": False, "context_tokens": 48000, "preset": "aggressive"}
    )
    assert old.mcp_purposes is False
