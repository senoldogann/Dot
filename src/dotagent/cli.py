"""The ``dotx`` command line: run tasks, chat, inspect models and keys, and diagnose the setup.

This module builds the parser and validates a command (:mod:`dotagent.cli_commands`); what carries a command out lives
in one module per family (``cli_run``, ``cli_daemon``, ...), loaded by name once the command is known, so that
``dotx --help`` and the light commands start without the server, the desktop, the MCP client or the eval runner.
"""

import argparse
import asyncio
import importlib
import sys
from typing import Protocol, cast, get_args

from pydantic import ValidationError

from dotagent.cli_commands import COMMAND_ADAPTER, Command
from dotagent.core.errors import DotError
from dotagent.core.heap import ensure_space_efficient_heap
from dotagent.core.logs import configure_logging, get_logger
from dotagent.core.paths import ensure_layout, log_dir
from dotagent.llm.types import KNOWN_PROVIDERS, Effort
from dotagent.policy.types import ApprovalMode
from dotagent.store.models import BrowserMode, MemoryOrder

_EFFORT_HELP: str = "reasoning level; the levels a model offers are listed by 'dotx models'"
_APPROVALS_HELP: str = (
    "who answers what the policy would ask: supervised (you, and before every change too), ask (you; edits and "
    "routine work run), auto (a review model) or yolo (nobody: nothing is asked, money and a message or a post in "
    "your name included, and each step is recorded with what it would have asked). A change of Dot's own files asks "
    "you in every mode but yolo"
)
_GOAL_MODE_HELP: str = (
    "a checker sends the run back to work until the goal is really reached (default: [agent] goal_mode)"
)
_PRUNE_HELP: str = (
    "report what can go: the events of runs that ended over [store] retention_days ago, the screenshot files that no "
    "chat names any more, the undo journals older than [agent] undo_keep_days, and the free space in the database file"
)
# The module that carries out each command, by the command's name; loaded only once that command runs.
_FAMILIES: dict[str, str] = {
    "run": "dotagent.cli_run",
    "resume": "dotagent.cli_run",
    "chat": "dotagent.cli_run",
    "models": "dotagent.cli_models",
    "auth-set": "dotagent.cli_models",
    "auth-status": "dotagent.cli_models",
    "models-profile": "dotagent.cli_profile",
    "doctor": "dotagent.cli_doctor",
    "setup": "dotagent.cli_setup",
    "reflect": "dotagent.cli_reflect",
    "browser-login": "dotagent.cli_browser",
    "browser-import-chrome": "dotagent.cli_browser",
    "browser-attach": "dotagent.cli_browser",
    "browser-detach": "dotagent.cli_browser",
    "desktop-check": "dotagent.cli_desktop",
    "desktop-setup": "dotagent.cli_desktop",
    "desktop-apps": "dotagent.cli_desktop",
    "desktop-snapshot": "dotagent.cli_desktop",
    "desktop-screenshot": "dotagent.cli_desktop",
    "memory-list": "dotagent.cli_memory",
    "memory-add": "dotagent.cli_memory",
    "memory-search": "dotagent.cli_memory",
    "memory-forget": "dotagent.cli_memory",
    "memory-proposals": "dotagent.cli_memory",
    "memory-keep": "dotagent.cli_memory",
    "memory-trash": "dotagent.cli_memory",
    "memory-restore": "dotagent.cli_memory",
    "memory-purge": "dotagent.cli_memory",
    "memory-pin": "dotagent.cli_memory",
    "memory-unpin": "dotagent.cli_memory",
    "memory-unmerge": "dotagent.cli_memory",
    "memory-reindex": "dotagent.cli_memory",
    "harness-list": "dotagent.cli_harness",
    "harness-show": "dotagent.cli_harness",
    "harness-diff": "dotagent.cli_harness",
    "harness-set": "dotagent.cli_harness",
    "harness-adopt": "dotagent.cli_harness",
    "harness-retire": "dotagent.cli_harness",
    "harness-propose": "dotagent.cli_harness",
    "harness-step": "dotagent.cli_harness",
    "harness-probe": "dotagent.cli_harness",
    "harness-final": "dotagent.cli_harness",
    "harness-graph": "dotagent.cli_harness",
    "harness-steps": "dotagent.cli_harness",
    "harness-experiences": "dotagent.cli_harness",
    "schedule-list": "dotagent.cli_schedule",
    "schedule-add": "dotagent.cli_schedule",
    "schedule-delete": "dotagent.cli_schedule",
    "schedule-enable": "dotagent.cli_schedule",
    "schedule-disable": "dotagent.cli_schedule",
    "campaign-list": "dotagent.cli_campaign",
    "campaign-show": "dotagent.cli_campaign",
    "campaign-stop": "dotagent.cli_campaign",
    "campaign-resume": "dotagent.cli_campaign",
    "campaign-achieved": "dotagent.cli_campaign",
    "mcp-list": "dotagent.cli_mcp",
    "skills-list": "dotagent.cli_skills",
    "skills-versions": "dotagent.cli_playbooks",
    "skills-restore": "dotagent.cli_playbooks",
    "skills-pin": "dotagent.cli_playbooks",
    "skills-unpin": "dotagent.cli_playbooks",
    "skills-rest": "dotagent.cli_playbooks",
    "skills-wake": "dotagent.cli_playbooks",
    "skills-proposals": "dotagent.cli_playbooks",
    "skills-accept": "dotagent.cli_playbooks",
    "skills-dismiss": "dotagent.cli_playbooks",
    "playbooks-list": "dotagent.cli_playbooks",
    "playbooks-show": "dotagent.cli_playbooks",
    "playbooks-drop": "dotagent.cli_playbooks",
    "playbooks-trust": "dotagent.cli_playbooks",
    "prune": "dotagent.cli_prune",
    "backup-now": "dotagent.cli_backup",
    "backup-list": "dotagent.cli_backup",
    "undo": "dotagent.cli_undo",
    "eval-list": "dotagent.cli_eval",
    "eval-check": "dotagent.cli_eval",
    "eval-run": "dotagent.cli_eval",
    "eval-report": "dotagent.cli_eval",
    "stats": "dotagent.cli_stats",
    "daemon": "dotagent.cli_daemon",
    "open": "dotagent.cli_daemon",
    "url": "dotagent.cli_daemon",
    "service-install": "dotagent.cli_daemon",
    "service-uninstall": "dotagent.cli_daemon",
    "service-status": "dotagent.cli_daemon",
    "service-permissions": "dotagent.cli_daemon",
    "service-restart": "dotagent.cli_daemon",
}

log = get_logger(__name__)


class _Family(Protocol):
    async def dispatch(self, command: Command) -> int: ...


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dotx", description="Dot: an autonomous agent for your Mac.")
    commands = parser.add_subparsers(dest="name", required=True)

    run = commands.add_parser("run", help="carry out a goal and print the result")
    run.add_argument("goal", nargs="+", help="the goal in plain words; '-' reads it from standard input")
    run.add_argument("--mode", choices=["task", "research"], default="task", help="research is read-only")
    run.add_argument("--model", default=None, help="provider:model, e.g. ollama:gpt-oss:20b-cloud")
    run.add_argument("--effort", default=None, choices=list(get_args(Effort)), help=_EFFORT_HELP)
    run.add_argument("--approvals", default=None, choices=list(get_args(ApprovalMode)), help=_APPROVALS_HELP)
    run.add_argument("--yolo", dest="approvals", action="store_const", const="yolo", help="same as --approvals yolo")
    run.add_argument(
        "--auto-approve", dest="approvals", action="store_const", const="auto", help="same as --approvals auto"
    )
    run.add_argument("--goal-mode", action=argparse.BooleanOptionalAction, default=None, help=_GOAL_MODE_HELP)
    run.add_argument(
        "--standing",
        action="store_true",
        help=(
            "keep going until done: start a campaign on the goal with this run as its first session; the daemon runs "
            "the sessions after it until the outcome is verified (a task, without goal mode)"
        ),
    )
    run.add_argument(
        "--browser",
        default=None,
        choices=list(get_args(BrowserMode.__value__)),
        help=(
            "with --standing: the Chrome the campaign works in, attach (the one you have open, signed in as you are) "
            "or own (Dot's own); the default is [browser] mode"
        ),
    )
    run.add_argument("--thread", default=None, help="continue an existing conversation thread")
    run.add_argument("--no-input", action="store_true", help="never prompt; approvals are denied")
    run.add_argument("--thinking", action="store_true", help="show the model's reasoning stream")
    run.add_argument("--verbose", action="store_true", help="show tool output and token use")

    resume = commands.add_parser(
        "resume",
        help=(
            "go on with a run that a restart or a shutdown of the daemon, or a closed terminal, cut off, and print "
            "the result"
        ),
    )
    resume.add_argument(
        "run_id",
        help="the id of the run that was cut off: the last line of 'dotx run' and the run page's address show it",
    )
    resume.add_argument("--no-input", action="store_true", help="never prompt; approvals are denied")
    resume.add_argument("--thinking", action="store_true", help="show the model's reasoning stream")
    resume.add_argument("--verbose", action="store_true", help="show tool output and token use")

    chat = commands.add_parser("chat", help="talk with Dot interactively")
    chat.add_argument("--model", default=None, help="provider:model")
    chat.add_argument("--effort", default=None, choices=list(get_args(Effort)), help=_EFFORT_HELP)
    chat.add_argument("--approvals", default=None, choices=list(get_args(ApprovalMode)), help=_APPROVALS_HELP)
    chat.add_argument("--yolo", dest="approvals", action="store_const", const="yolo", help="same as --approvals yolo")
    chat.add_argument(
        "--auto-approve", dest="approvals", action="store_const", const="auto", help="same as --approvals auto"
    )
    chat.add_argument("--thread", default=None, help="continue an existing conversation thread")
    chat.add_argument("--thinking", action="store_true", help="show the model's reasoning stream")
    chat.add_argument("--verbose", action="store_true", help="show tool output and token use")

    models = commands.add_parser("models", help="list the models the providers offer, or profile one of them")
    models_commands = models.add_subparsers(dest="models_action", required=False)
    for provider_name in KNOWN_PROVIDERS:
        models_commands.add_parser(provider_name, help=f"list the models {provider_name} offers")
    models_profile = models_commands.add_parser(
        "profile", help="how reliably a model worked in its newest runs, and the help its next run gets"
    )
    models_profile.add_argument("model", nargs="?", default=None, help="provider:model; [models] main when left out")

    auth = commands.add_parser("auth", help="manage API keys")
    auth_commands = auth.add_subparsers(dest="auth_action", required=True)
    auth_set = auth_commands.add_parser("set", help="store an API key (read from the terminal, never echoed)")
    auth_set.add_argument("key", help="variable name, e.g. OPENAI_API_KEY")
    auth_commands.add_parser("status", help="show which API keys are present")

    commands.add_parser("doctor", help="check the setup")
    setup = commands.add_parser(
        "setup", help="check this Mac, choose the model Dot runs on and store its key, and install the service"
    )
    setup.add_argument(
        "--model", default=None, help="provider:model, e.g. openai:gpt-5.5; a terminal asks for it when left out"
    )
    setup.add_argument(
        "--key-stdin",
        action="store_true",
        help="read the provider's API key from standard input and store it; without it a key already set is kept",
    )
    setup.add_argument("--no-service", action="store_true", help="leave the launchd service as it is")
    commands.add_parser(
        "reflect",
        help=(
            "reflect now on the runs that ended since the last reflection: lessons, merged and rested lesson notes, "
            "the day's journal note (the daemon does it at [agent] reflection_time)"
        ),
    )

    browser = commands.add_parser("browser", help="manage the browser Dot uses")
    browser_commands = browser.add_subparsers(dest="browser_action", required=True)
    browser_login = browser_commands.add_parser(
        "login", help="open Dot's Chrome profile as a normal window so you can sign in to sites"
    )
    browser_login.add_argument("url", nargs="?", default=None, help="page to open first, e.g. accounts.google.com")
    browser_import = browser_commands.add_parser(
        "import-chrome",
        help=(
            "copy the sign-ins (cookies, local storage) of a personal Chrome profile into Dot's profile; quit Chrome "
            "first"
        ),
    )
    browser_import.add_argument(
        "--profile", default="Default", help="Chrome profile directory, e.g. Default or 'Profile 1'"
    )
    browser_import.add_argument(
        "--replace", action="store_true", help="replace the sign-ins that Dot's own profile already holds"
    )
    browser_commands.add_parser(
        "attach",
        help="work in new tabs of the Chrome you already have open, signed in as you are (Chrome 144 or later)",
    )
    browser_commands.add_parser("detach", help="go back to Dot's own Chrome and profile")

    desktop = commands.add_parser("desktop", help="check and try the desktop tools (screen, mouse, keyboard, apps)")
    desktop_commands = desktop.add_subparsers(dest="desktop_action", required=True)
    desktop_commands.add_parser("check", help="show which macOS privacy permissions Dot has")
    desktop_commands.add_parser("setup", help="ask macOS for the missing permissions and open their Settings panes")
    desktop_commands.add_parser("apps", help="list the open applications and their windows")
    desktop_snapshot = desktop_commands.add_parser(
        "snapshot", help="print an application's window as the agent sees it"
    )
    desktop_snapshot.add_argument("app", nargs="?", default=None, help="application name (default: the one in front)")
    desktop_snapshot.add_argument(
        "--scope", choices=["window", "all_windows", "menu_bar"], default="window", help="what part to show"
    )
    desktop_snapshot.add_argument("--query", default=None, help="only elements whose text contains this")
    desktop_screenshot = desktop_commands.add_parser("screenshot", help="save a screenshot of the main display")
    desktop_screenshot.add_argument("path", help="where to write the JPEG")

    memory = commands.add_parser("memory", help="see and edit the notes Dot remembers between runs")
    memory_commands = memory.add_subparsers(dest="memory_action", required=True)
    memory_list = memory_commands.add_parser("list", help="show the saved notes, the most recently changed first")
    memory_list.add_argument("--limit", type=int, default=50, help="how many notes to show")
    memory_list.add_argument(
        "--order",
        choices=list(get_args(MemoryOrder.__value__)),
        default="changed",
        help="changed: the last changed first; used: the last used first; most_used; least_used: never used first",
    )
    memory_add = memory_commands.add_parser("add", help="save a note: one short fact")
    memory_add.add_argument("text", nargs="+", help="the note")
    memory_add.add_argument(
        "--tag", dest="tags", action="append", default=[], help="a topic word; 'core' puts the note into every run"
    )
    memory_search = memory_commands.add_parser("search", help="find notes by meaning and by words")
    memory_search.add_argument("query", nargs="+", help="what to look for")
    memory_search.add_argument("--limit", type=int, default=8, help="how many notes to show at most")
    memory_forget = memory_commands.add_parser(
        "forget",
        help="forget a note: it waits in the trash for [memory] trash_days, where 'dotx memory restore' brings it back",
    )
    # One note by its id, or every proposed one: argparse says which is missing, and refuses both.
    forget_what = memory_forget.add_mutually_exclusive_group(required=True)
    forget_what.add_argument(
        "memory_id", nargs="?", default=None, help="the id shown in brackets by 'dotx memory list'"
    )
    forget_what.add_argument(
        "--proposed",
        action="store_true",
        help="forget every note 'dotx memory proposals' lists, instead of one by its id",
    )
    memory_commands.add_parser(
        "proposals", help="the notes that rested for [memory] forget_after_days and wait for you to forget or keep them"
    )
    memory_keep = memory_commands.add_parser("keep", help="keep proposed notes: they wake and rest again only later")
    memory_keep.add_argument("memory_ids", nargs="+", help="the ids 'dotx memory proposals' shows")
    memory_commands.add_parser("trash", help="the notes you forgot, with when each is deleted for good")
    memory_restore = memory_commands.add_parser("restore", help="bring a forgotten note back from the trash, awake")
    memory_restore.add_argument("memory_id", help="the id 'dotx memory trash' shows")
    memory_purge = memory_commands.add_parser("purge", help="delete a note of the trash for good now")
    memory_purge.add_argument("memory_id", help="the id 'dotx memory trash' shows")
    memory_pin = memory_commands.add_parser("pin", help="pin a note, so that it never rests (a resting note wakes)")
    memory_pin.add_argument("memory_id", help="the id shown in brackets by 'dotx memory list'")
    memory_unpin = memory_commands.add_parser("unpin", help="take the pin off a note")
    memory_unpin.add_argument("memory_id", help="the id shown in brackets by 'dotx memory list'")
    memory_unmerge = memory_commands.add_parser(
        "unmerge", help="take back a merge: the notes it put to rest wake and the merged note goes to the trash"
    )
    memory_unmerge.add_argument("memory_id", help="the id of the merged note")
    memory_commands.add_parser("reindex", help="embed every note again, e.g. after changing [memory] embed_model")

    harness = commands.add_parser(
        "harness", help="the versions of what Dot's prompt takes from the harness (docs/harness.md)"
    )
    harness_commands = harness.add_subparsers(dest="harness_action", required=True)
    harness_commands.add_parser("list", help="every version, the newest first, with the adopted one marked")
    harness_show = harness_commands.add_parser("show", help="the texts of a version (the adopted one by default)")
    harness_show.add_argument("version_id", nargs="?", default=None, help="a version id from 'dotx harness list'")
    harness_diff = harness_commands.add_parser("diff", help="what a version changes against another")
    harness_diff.add_argument("version_id", help="the version to read")
    harness_diff.add_argument(
        "--against", default=None, help="the version compared with; the adopted one, or Dot's own text, by default"
    )
    harness_set = harness_commands.add_parser("set", help="write a version of your own from files, and adopt it or not")
    harness_set.add_argument("--summary", required=True, help="what the version is, in a line")
    harness_set.add_argument("--habits", default=None, help="a file with the habits ('-' reads standard input)")
    harness_set.add_argument(
        "--tool-guidance",
        dest="tool_guidance",
        default=None,
        help="a file with the tool guidance ('-' for standard input)",
    )
    harness_set.add_argument("--adopt", action="store_true", help="adopt the version at once")
    harness_adopt = harness_commands.add_parser("adopt", help="make a version the one every run takes")
    harness_adopt.add_argument("version_id", help="a version id from 'dotx harness list'")
    harness_adopt.add_argument(
        "--yes",
        action="store_true",
        help="adopt a version written from content others can author without asking first",
    )
    harness_retire = harness_commands.add_parser(
        "retire", help="put a version aside; an adopted one leaves the prompt as Dot's own"
    )
    harness_retire.add_argument("version_id", help="a version id from 'dotx harness list'")
    harness_propose = harness_commands.add_parser(
        "propose", help="ask the proposer for a candidate from what one run did (a model call; the candidate waits)"
    )
    harness_propose.add_argument("run_id", help="the run to learn from, as its page or dotx stats --run names it")
    harness_propose.add_argument(
        "--scope",
        choices=["invariant", "domain"],
        default="invariant",
        help="how the agent works (invariant, the default) or what a practitioner knows (domain)",
    )
    harness_step = harness_commands.add_parser(
        "step",
        help=(
            "one step of the improvement loop now: propose, probe the scoring set apart, keep the best, write the"
            " experience (the daemon does it at [harness] step_time)"
        ),
    )
    harness_step.add_argument(
        "--run",
        dest="run_id",
        default=None,
        help="the run to learn from, the one that went worst by default; a step that only tries the pool leaves it"
        " for the next that proposes",
    )
    harness_step.add_argument(
        "--scripted", action="store_true", help="play the references through the scripted model: a check, no model cost"
    )
    harness_probe = harness_commands.add_parser("probe", help="a probe in a process of its own (the step starts these)")
    harness_probe.add_argument(
        "--ask", required=True, help="a JSON file with what to play; the report is printed as JSON"
    )
    harness_final = harness_commands.add_parser(
        "final", help="play the final set once under the adopted version and report it as it comes out"
    )
    harness_final.add_argument("version_id", nargs="?", default=None, help="a version other than the adopted one")
    harness_graph = harness_commands.add_parser(
        "graph", help="the provenance of the harness as a Mermaid flowchart: versions, steps, experiences, runs"
    )
    harness_graph.add_argument("--steps", type=int, default=50, help="how many of the newest steps to draw")
    harness_steps = harness_commands.add_parser("steps", help="the newest steps of the improvement loop")
    harness_steps.add_argument("--limit", type=int, default=20, help="how many steps to show")
    harness_experiences = harness_commands.add_parser("experiences", help="the experience library, the newest first")
    harness_experiences.add_argument("--limit", type=int, default=50, help="how many experiences to show")

    schedule = commands.add_parser("schedule", help="goals that Dot runs later or on a rhythm, while the daemon runs")
    schedule_commands = schedule.add_subparsers(dest="schedule_action", required=True)
    schedule_commands.add_parser("list", help="show every schedule: when it runs, what is next, how the last run went")
    schedule_add = schedule_commands.add_parser("add", help="create a schedule")
    schedule_add.add_argument("--name", dest="title", required=True, help="a short label")
    schedule_timing = schedule_add.add_mutually_exclusive_group(required=True)
    schedule_timing.add_argument(
        "--cron", default=None, help="repeat on five fields in local time, e.g. '0 9 * * 1-5' for 09:00 on weekdays"
    )
    schedule_timing.add_argument(
        "--at", default=None, help="run once at this local date and time, e.g. 2026-10-06T09:30"
    )
    schedule_add.add_argument("--mode", choices=["task", "research"], default="task", help="research is read-only")
    schedule_add.add_argument("goal", nargs="+", help="the complete goal; each run starts without any conversation")
    schedule_delete = schedule_commands.add_parser("delete", help="delete a schedule")
    schedule_delete.add_argument("schedule_id", help="the id shown by 'dotx schedule list'")
    schedule_enable = schedule_commands.add_parser("enable", help="switch a schedule on again")
    schedule_enable.add_argument("schedule_id", help="the id shown by 'dotx schedule list'")
    schedule_disable = schedule_commands.add_parser("disable", help="switch a schedule off without deleting it")
    schedule_disable.add_argument("schedule_id", help="the id shown by 'dotx schedule list'")

    campaign = commands.add_parser(
        "campaign", help="goals that Dot keeps working on in sessions until done ('dotx run --standing' starts one)"
    )
    campaign_commands = campaign.add_subparsers(dest="campaign_action", required=True)
    campaign_commands.add_parser("list", help="show every campaign: where it stands and how many sessions it ran")
    campaign_show = campaign_commands.add_parser("show", help="show a campaign: its targets, sessions and ledger")
    campaign_show.add_argument("campaign_id", help="the id shown by 'dotx campaign list'")
    campaign_stop = campaign_commands.add_parser("stop", help="stop a campaign: no session starts after it")
    campaign_stop.add_argument("campaign_id", help="the id shown by 'dotx campaign list'")
    campaign_resume = campaign_commands.add_parser(
        "resume", help="let a stopped or achieved campaign go on: its next session starts now"
    )
    campaign_resume.add_argument("campaign_id", help="the id shown by 'dotx campaign list'")
    campaign_achieved = campaign_commands.add_parser(
        "achieved", help="mark a campaign achieved on your word, without the goal check"
    )
    campaign_achieved.add_argument("campaign_id", help="the id shown by 'dotx campaign list'")

    mcp = commands.add_parser("mcp", help="the MCP tool servers that Dot connects to")
    mcp_commands = mcp.add_subparsers(dest="mcp_action", required=True)
    mcp_commands.add_parser(
        "list", help="start each configured server and show the tools it offers, or why it does not start"
    )

    skills = commands.add_parser("skills", help="the skills and plugins the agent can use")
    skills_commands = skills.add_subparsers(dest="skills_action", required=True)
    skills_commands.add_parser("list", help="show every skill and plugin found, and the folders left out and why")
    skills_versions = skills_commands.add_parser(
        "versions", help="the versions of a learned skill: when and from which run each was made, and what it changed"
    )
    skills_versions.add_argument("skill", help="the name shown by 'dotx playbooks list'")
    skills_restore = skills_commands.add_parser(
        "restore", help="put a version of a learned skill back as its steps, as a new version of its own"
    )
    skills_restore.add_argument("skill", help="the name shown by 'dotx playbooks list'")
    skills_restore.add_argument("version", type=int, help="the number 'dotx skills versions' shows")
    skills_pin = skills_commands.add_parser(
        "pin", help="pin a learned skill, so that it never rests (a resting one wakes)"
    )
    skills_pin.add_argument("skill", help="the name shown by 'dotx playbooks list'")
    skills_unpin = skills_commands.add_parser("unpin", help="take the pin off a learned skill")
    skills_unpin.add_argument("skill", help="the name shown by 'dotx playbooks list'")
    skills_rest = skills_commands.add_parser(
        "rest", help="put a learned skill to rest: runs are not offered it until you wake it"
    )
    skills_rest.add_argument("skill", help="the name shown by 'dotx playbooks list'")
    skills_wake = skills_commands.add_parser(
        "wake", help="wake a learned skill that rests, for disuse, by hand or for a merge"
    )
    skills_wake.add_argument("skill", help="the name shown by 'dotx playbooks list'")
    skills_commands.add_parser(
        "proposals", help="the merges of learned skills that wait for your yes, with the steps each would write"
    )
    skills_accept = skills_commands.add_parser(
        "accept", help="say yes to a proposal: its steps become a new version of the first skill, the other one rests"
    )
    skills_accept.add_argument("proposal_id", help="the id 'dotx skills proposals' shows")
    skills_dismiss = skills_commands.add_parser(
        "dismiss", help="say no to a proposal: it is dropped and not proposed again until one of its skills changes"
    )
    skills_dismiss.add_argument("proposal_id", help="the id 'dotx skills proposals' shows")

    playbooks = commands.add_parser("playbooks", help="the playbooks Dot distilled from runs that reached their goal")
    playbooks_commands = playbooks.add_subparsers(dest="playbooks_action", required=True)
    playbooks_commands.add_parser("list", help="show every playbook, how often it was used and whether it is retired")
    playbooks_show = playbooks_commands.add_parser("show", help="print a playbook's instructions")
    playbooks_show.add_argument("playbook", help="the name shown by 'dotx playbooks list'")
    playbooks_drop = playbooks_commands.add_parser("drop", help="remove a playbook, active or retired")
    playbooks_drop.add_argument("playbook", help="the name shown by 'dotx playbooks list'")
    playbooks_trust = playbooks_commands.add_parser(
        "trust", help="offer a playbook learned from third-party content, or of unknown origin, with all its steps"
    )
    playbooks_trust.add_argument("playbook", help="the name shown by 'dotx playbooks list'")

    prune = commands.add_parser("prune", help=_PRUNE_HELP, description=_PRUNE_HELP)
    prune.add_argument(
        "--apply",
        action="store_true",
        help=(
            "delete those events (the runs keep their answers, not their timelines), screenshots and undo journals "
            "(the file changes of their runs can no longer be taken back), and give the free space back to the disk "
            "(without it, only report)"
        ),
    )

    backup = commands.add_parser("backup", help="the copies of Dot's database: make one now, or list them")
    backup_commands = backup.add_subparsers(dest="backup_action", required=True)
    backup_commands.add_parser("now", help="copy the database now into [store] backup_dir and thin the older copies")
    backup_commands.add_parser("list", help="list the copies kept, newest first")

    undo = commands.add_parser(
        "undo",
        help="take back the file changes that write_file and edit_file made in a run and in the runs it delegated to",
    )
    undo.add_argument(
        "run_id", help="the id of the run: the last line of 'dotx run' and the run page's address show it"
    )
    undo.add_argument(
        "--apply", action="store_true", help="take the changes back (without it, only show what would be done)"
    )
    undo.add_argument(
        "--force",
        action="store_true",
        help="with --apply, also take back the files that changed after the run's last change to them",
    )

    evaluation = commands.add_parser(
        "eval", help="measure how well Dot finishes tasks with a model: tasks with checks, and a score"
    )
    eval_commands = evaluation.add_subparsers(dest="eval_action", required=True)
    eval_commands.add_parser("list", help="list the eval tasks")
    eval_commands.add_parser(
        "check",
        help="prove, offline and without a model, that every task can be passed and cannot be passed by doing nothing",
    )
    eval_run = eval_commands.add_parser(
        "run", help="play the tasks against a model and print the score (this spends the tokens of that model)"
    )
    eval_run.add_argument(
        "--model", default=None, help="provider:model; the main model of the configuration if omitted"
    )
    eval_run.add_argument(
        "--task", dest="tasks", action="append", default=[], help="play only this task; give it again for more"
    )
    eval_run.add_argument(
        "--repeat", type=int, default=1, help="play every task this many times: one attempt says little about a model"
    )
    eval_run.add_argument(
        "--no-mcp-purposes",
        action="store_true",
        help="leave the line of each MCP server's tool group as it was before it said what the server is for "
        "([agent] mcp_purposes = false for this run), to measure what those words change",
    )
    eval_report = eval_commands.add_parser(
        "report", help="show the score of saved eval runs and compare them (all saved runs if no file is given)"
    )
    eval_report.add_argument("files", nargs="*", help="report files written by 'dotx eval run'")

    stats = commands.add_parser(
        "stats", help="numbers about the runs that really happened: how they ended, tokens and tool errors per model"
    )
    stats.add_argument("--days", type=int, default=30, help="only the runs of the last N days")
    stats.add_argument(
        "--by",
        choices=["model", "site", "all"],
        default="all",
        help="what to show: the numbers per model, the act calls per site (a page's host or an application), or both",
    )
    stats.add_argument(
        "--run", metavar="ID", default=None, help="the numbers of one run instead: what it cost and where"
    )

    commands.add_parser("daemon", help="run Dot in this terminal: the API, the web UI, the agent and the schedules")
    commands.add_parser("open", help="open the web UI of the running daemon in your default browser, signed in")
    commands.add_parser("url", help="print the address of the web UI, with the sign-in token in it")

    service = commands.add_parser("service", help="keep the daemon running in the background (a launchd job)")
    service_commands = service.add_subparsers(dest="service_action", required=True)
    install = service_commands.add_parser(
        "install", help="start the daemon at every login and after a crash; also restarts it"
    )
    install.add_argument(
        "--launcher",
        metavar="PATH",
        default=None,
        help=(
            "the full path of a prebuilt, signed program for Dot.app (the desktop app carries one), copied in place of "
            "the one built with clang, so that Xcode's Command Line Tools are not needed; DOT_APP_LAUNCHER names it "
            "too, and left out, an app made from one is made again from the program it keeps"
        ),
    )
    service_commands.add_parser("uninstall", help="stop the daemon and remove the launchd job")
    service_commands.add_parser("status", help="show whether the job is loaded and the daemon answers")
    service_commands.add_parser(
        "permissions", help="make macOS ask, in the name of the service, for the desktop permissions it lacks"
    )
    restart = service_commands.add_parser("restart", help="restart the daemon now, or once no run works")
    restart.add_argument(
        "--when-idle",
        action="store_true",
        help=(
            "return at once and restart the daemon when no run works (the run that asks included); a run that "
            "changed Dot's code asks for this, never for a launchd job of its own"
        ),
    )
    return parser


def parse_command(parser: argparse.ArgumentParser, arguments: list[str]) -> Command:
    namespace: argparse.Namespace = parser.parse_args(arguments)
    raw: dict[str, object] = dict(vars(namespace))
    action: object = raw.pop("auth_action", None)
    browser_action: object = raw.pop("browser_action", None)
    desktop_action: object = raw.pop("desktop_action", None)
    memory_action: object = raw.pop("memory_action", None)
    harness_action: object = raw.pop("harness_action", None)
    schedule_action: object = raw.pop("schedule_action", None)
    campaign_action: object = raw.pop("campaign_action", None)
    mcp_action: object = raw.pop("mcp_action", None)
    skills_action: object = raw.pop("skills_action", None)
    playbooks_action: object = raw.pop("playbooks_action", None)
    service_action: object = raw.pop("service_action", None)
    eval_action: object = raw.pop("eval_action", None)
    backup_action: object = raw.pop("backup_action", None)
    models_action: object = raw.pop("models_action", None)
    if raw["name"] == "models" and models_action == "profile":
        raw["name"] = "models-profile"
    elif raw["name"] == "models":
        raw["provider"] = models_action
    if raw["name"] == "auth":
        raw["name"] = f"auth-{action}"
    if raw["name"] == "browser":
        raw["name"] = f"browser-{browser_action}"
    if raw["name"] == "desktop":
        raw["name"] = f"desktop-{desktop_action}"
    if raw["name"] == "memory":
        raw["name"] = f"memory-{memory_action}"
    if raw["name"] == "harness":
        raw["name"] = f"harness-{harness_action}"
    if raw["name"] == "schedule":
        raw["name"] = f"schedule-{schedule_action}"
    if raw["name"] == "campaign":
        raw["name"] = f"campaign-{campaign_action}"
    if raw["name"] == "mcp":
        raw["name"] = f"mcp-{mcp_action}"
    if raw["name"] == "skills":
        raw["name"] = f"skills-{skills_action}"
    if raw["name"] == "playbooks":
        raw["name"] = f"playbooks-{playbooks_action}"
    if raw["name"] == "service":
        raw["name"] = f"service-{service_action}"
    if raw["name"] == "eval":
        raw["name"] = f"eval-{eval_action}"
    if raw["name"] == "backup":
        raw["name"] = f"backup-{backup_action}"
    try:
        return COMMAND_ADAPTER.validate_python(raw)
    except ValidationError as exc:
        parser.error(str(exc))


async def dispatch(command: Command) -> int:
    """Carry out ``command`` in the module of its family."""
    family: _Family = cast(_Family, importlib.import_module(_FAMILIES[command.name]))
    return await family.dispatch(command)


def main() -> None:
    parser: argparse.ArgumentParser = build_parser()
    command: Command = parse_command(parser, sys.argv[1:])
    if command.name == "daemon":
        # Before anything is opened: the daemon starts itself again with malloc's space-efficient mode (item 730).
        ensure_space_efficient_heap()
    ensure_layout()
    configure_logging("info", False, log_dir() / "dotx.log")
    try:
        code: int = asyncio.run(dispatch(command))
    except DotError as exc:
        # On record in dotx.log too, so that a daemon that keeps failing to start (a configuration it refuses, an
        # address taken) says why where the log is read, not only in the stderr of its launchd job.
        log.error("command_failed", command=command.name, error=exc.message, context=exc.context)
        sys.stderr.write(f"error: {exc}\n")
        code = 2
    except (KeyboardInterrupt, asyncio.CancelledError):
        code = 130
    sys.exit(code)


if __name__ == "__main__":
    main()
