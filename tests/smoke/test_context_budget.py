"""What a run's first step carries in a fresh home (item 735): the system prompt and the tools' schemas, recorded as a
``context_parts`` event and held under a budget, so that it no longer grows unseen (a fresh scheduled run of the
owner's went from 49,000 to 61,000 tokens before its first step in two weeks). A change that needs more raises the
budget here, saying why; one that saves some lowers it. No model service is used."""

import asyncio
import json

import pytest

from dotagent.agent.context_parts import CONTEXT_PARTS_EVENT, ContextParts
from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)
)
# The budget of a fresh home's task run, every tool offered and the full prompt, in Dot's estimate of tokens (3.2
# characters each). Measured 2026-10-27: 20,221 in all, the system prompt 6,692, the schemas of 54 tools 13,502 (the
# core tools 20,406 characters, the browser's 11,440). Raise it with a reason; lower it when a change saves some.
# Raised by 800 for three browser tools and their lines (21,072 measured): browser_create_login, so that no password of
# a new account is made up by a model and written into its history (item 740), and browser_console and
# browser_viewport, to see why a page does not work and how it lays out on a phone (item 746).
# Raised by 200 for `recon` (item 776): one tool, 985 characters of schema, the audit of a site's public face and the
# ports of an owned host (21,401 measured).
# Raised by 600 for `security_scan` (item 781) and the selector and retry of the browser tools: a form's payloads on
# an owned host, one tool of 1,063 tokens (21,984 measured).
# Raised by 700 for the captcha skill and its prompt rule, and `drag_at` on browser_act (item 792): a human check is
# solved in the page, so the rule and the drag's two coordinates are carried by every run (22,466 measured; system
# 7,148, tools 15,292).
# Raised by 100 for the four rules of work in the owner's projects (item 851, the owner's word of 2026-10-30 after a
# day of Dot's initiative on another project): a worktree of its own, the project's release, the product's source, its
# own plan (22,855 measured; system 7,355).
# Raised by 300 for `ask_secret` (item 857): a run asks the owner for a key on a card of its own, the value kept out of
# the chat, rather than in a message whose answer the history keeps (23,111 measured; tools 15,730).
# Raised by 200 for `create_team` (item 864): asked for a team, a run made its agents one by one and could not make the
# team of them (23,290 measured; tools 15,892).
# Raised by 300 for `wait_until` (item 887): a run waits for a file, an address or a check in code, where runs of one
# week of the owner's slept and polled 131 times, each poll a model turn; its words cut to 316 tokens (23,606
# measured; tools 16,190).
# Raised by 100 for `nothing_new` on `finish` (item 880: a scheduled client mail check told its owner seven times in a
# day that nobody had answered) and `refuses` on `create_agent` (item 875: an agent Dot makes has the edges of its job),
# their descriptions cut to a line each (23,725 measured with item 887; tools 16,299).
# Raised by 200 for `agents_now` and `read_agent_chat` (item 890: an agent sees what the others do and reads one's
# chat to help it, the owner's ask of 2026-10-31), their descriptions a line each (23,901 measured with items 880 and
# 875; tools 16,476).
# Raised by 200 for `skill_save` (item 893, after a guide to Grok's bots the owner sent: "tell your bots to create
# skills for itself as you give it instructions"): a run writes down how the owner told it to do a kind of work, in a
# group of its own (24,110 measured with item 890; tools 16,684).
# Raised by 1,200 for the work orders and the objectives (items 907, 908, the owner's ask of 2026-11-01: Dot runs its
# agents as a CEO runs staff): `assign_work`, `work_orders` and `review_work` (the lead hands work out without waiting
# and judges what comes back), `objective_add`, `objective_update` and `objectives` (what the owner wants moved
# forward), each described in a line, and the lead's paragraph on managing (25,356 measured; system 7,591, tools
# 17,739).
# Raised by 350 for the gaps the owner took from the DeepSeek Harness super-agent article (2026-11-02): `recall`, which
# reads back by its id a record a compaction note names (item 924), `pass_reply`, which hands a reply to a mail to the
# chat that sent it (item 926), and the `brief` of a plan's step, which runs the independent steps at once as helpers
# (item 927), each described in a line (25,713 measured; system 7,591, tools 18,097).
# Raised by 200 for `chats` and `read_chat` (item 953, the owner's ask of 2026-11-03: the chats know of each other): a
# run finds a chat of this Mac by its title and reads it, each described in a line (25,946 measured; system 7,591,
# tools 18,329).
# Raised by 100 for `send_to_chat` (item 957, the same ask): a note to the run at work in another chat, described in a
# line (26,049 measured; system 7,591, tools 18,432).
# Raised by 100 for the owner's rule of 2026-11-07 (item 1023): analyses thorough, recommendations bold, one line of
# the prompt's Communication (26,138 measured; system 7,688).
# Raised by 50 for the line of `show_page` that has a document shown as its text, not a picture of it (item 1056: the
# owner's CV, shown as a JPEG, blurred in the full-screen viewer; 26,162 measured).
# Raised by 50 for `review_work`'s `brief`, the rewritten brief an order that came back twice starts afresh on, and the
# word that it is accepted on its Verification (item 1065: the owner's lessons of managing agents; 26,238 measured).
# Raised by 50 for the machine, not for words (item 1095): the prompt names the home, the user and a scratch folder,
# whose lengths differ between this Mac and the gate's runner, and the gate measured 26,251 with nothing grown.
# Raised by 150 for the line of "There is always a way" that has a run do the owner's request however bluntly it is put,
# without a lecture or a watered-down version (item 1102, the owner's ask of 2026-11-10; 26,391 measured here).
# Raised by 350 for the meeting tools (item 1138, the owner's ask of 2026-11-12 after Grok Bot 1.0): `meeting_join`,
# `meeting_read`, `meeting_post` and `meeting_leave`, Dot in the owner's Google Meet call, each described in a line
# (26,755 measured; tools 18,954).
# Raised by 300 for what a browser step came to (item 1145, the owner's ask of 2026-11-13: browser use that depends
# less on the model): `expect_text` and `expect_url` on a step of `browser_act`, the words of its result (done is
# sent, verified is seen, uncertain is not sent again unseen) and one line of the prompt's Browser, each cut to a
# line after a first wording took 580 (27,069 measured here, on item 1143's words; system 7,847, tools 19,195).
# Raised by 100 for the line that has a run find a file by name with Spotlight (item 1188: `find` and `grep -r` over the
# home folder took 46 to 100 s a call in the owner's week to 2026-11-15, where `mdfind` answers in 0.4 s; 27,106
# measured).
# Raised by 600 for the owner's `CHARACTER` block (items 1210 and 1211): bold in method and conservative in
# consequence, the result before the account of it, the next step ready, three ways before a road is closed and none
# around a refusal, and what he asks in his own name done within the run's approvals (27,620 measured here; the block
# is 1,614 of the 8,405 of system).
# Raised by 150 for `watch_video` (item 1212), whose definition is listed with its group (27,904 measured here).
# Raised by 350 for the rest of the owner's `CHARACTER` block (item 1211, continued): a silent deliberation, remote
# access, malware analysis, exploitation, evasion and scraping answered whole as engineering. Its last stop went with
# it (28,062 measured here, Character 2,176 of 8,465 of system).
# Raised by 1,450 for continuing work (item 1227, the owner's ask of 2026-11-19): the five `pipeline_*` tools that keep
# a client ledger in its record file (a mail begun before it goes out, never sent twice, outcomes apart) and the six
# parts of how an objective's work is done, each cut to a line after a first wording took 1,800 (29,443 measured here).
# Raised by 300 for `browser_captcha` (item 1288, the owner's ask of 2026-11-21: a run waited too long at captchas and
# verification pages - a screenshot, a guessed pixel and a sleep apiece, and a "Press & Hold" never passed): one tool
# that finds the check in any frame, presses or holds it and waits for it to clear, and hands back its picture at twice
# its size when it needs eyes (29,891 measured here; tools 21,118, which is 400 more than the 20,850 before it).
# Raised by 200 for the `wait` field of `delegate` and the line of the Delegation section that says a part going on in
# the background closes no step (item 1351, the owner's ask of 2026-11-23: a message they send while a run waits on a
# helper makes it go on, and the helper's report comes to the chat when it ends): 30,150 measured here, the rest being
# room for the longer paths of the gate's runner.
# Raised by 150 for the hand-over line of the Delegation section and `delegate`'s description (item 1362: a delegated
# run starts with the plan, the files, the checks and the failures its parent already measured, so it does not read the
# same files again): 30,305 measured on the gate's runner, system 8,758, tools 21,377.
# Raised by 700 for `code_run` and the section of the prompt that teaches it (item 1501, the owner's decision of
# 2026-11-24: the model writes code that calls its tools): one tool of 1,064 characters of description and three fields,
# and four lines that hold no state of the run. 30,971 measured here, system 8,919, tools 21,882. What it spares is the
# other side of the account: a run that calls many tools, or reads a lot of data, writes a short program and reads what
# it prints.
# The appearance tools carry two validated palettes in the full tool surface.
# Raised by 250 for the worktree capture tool (item 1658), with 32,048 measured.
# Raised by 200 for `search_chats` (item 1740): one tool of a line and five short fields, which finds what was said in
# any chat and reads one run, in the office group; 32,279 measured here.
# Raised by 100 for `memory_save`'s `subject` (item 1729): a fact that changed replaces its older note; 32,367 measured
# here.
# Raised by 100 for `show_page`'s words on pictures, videos and a page too long (item 1721), the steered calls and
# OpenCode's tools (items 1720 and 1722); 32,420 measured here.
# Raised by 300 for `add_proof` (item 1853): a check that must exit 0 before the run is done, in a group of its own;
# 32,775 measured here.
# Raised by 400 for the security testing line of the character and the scope words of `recon` and `security_scan`
# (items 1887 and 1888, which item 1889 sits on): 33,116 measured on the gate's runner.
BUDGET_TOKENS: int = 33_250
# Raised by 100 for the line that has a run say what it does as it works, as a colleague would in a chat (item 830: the
# owner's screenshots of an app whose agents speak so, and the conversation without the steps; system 7,231 measured).
# Raised by 100 for the rules of item 851 above.
# Raised by 200 for the lead's paragraph on work orders (item 907).
# Raised by 100 for the rule of item 1023 above.
# Raised by 200 for the line of item 1102 above (system 7,838 measured here).
# Raised by 100 for the Spotlight line of item 1188 (7,901 measured on the CI runner, whose longer paths add to the
# Environment section; 7,899 here).
# Raised by 600 for the owner's `CHARACTER` block of items 1210 and 1211 (system 8,405 measured here).
# Raised by 300 with the block of item 1211 above: the gate's runner measures longer paths than this Mac, whose system
# reads 8,465 now.
# Raised by 150 for the Code section of item 1501 above (system 8,919 measured here).
# Raised by 150 for the security testing line of the character (item 1887): system 9,178 measured here.
SYSTEM_BUDGET_TOKENS: int = 9_200
# Item 802: the delegate goal and tool description ask for a `Verification:` line; the old ceiling was 15,300.
# Raised by 100 for `todo_drop` (item 821): a run takes back a thing it put on Dot's own list by mistake, which one did
# by changing Dot's database instead (22,648 measured; tools 15,474).
# Raised by 300 for `ask_secret` (item 857) above, by 200 for `create_team` (item 864), and by 300 for `wait_until`
# (item 887) above, and by 100 for items 880 and 875 above.
# Raised by 200 for the office's two tools (item 890) above.
# Raised by 200 for `skill_save` (item 893) above.
# Raised by 1,000 for the six tools of items 907 and 908 above.
# Raised by 350 for `recall`, `pass_reply` and a plan step's `brief` (items 924, 926 and 927) above.
# Raised by 200 for `chats` and `read_chat` (item 953) above.
# Raised by 100 for `send_to_chat` (item 957) above.
# Raised by 50 for the line of `show_page` of item 1056 above.
# Raised by 50 for `review_work`'s `brief` (item 1065) above (tools 18,516 measured).
# Raised by 450 for the four meeting tools of item 1138 above (18,954 measured).
# Raised by 200 for the two fields of a browser step and the words of its result of item 1145 above (19,195 measured).
# Raised by 300 for `watch_video` of item 1212 above (19,472 measured).
# Raised by 1,350 for the pipeline tools and the parts of an objective's work of item 1227 above (20,808 measured).
# Raised by 400 with `browser_captcha` of item 1288 above (21,118 measured).
# Raised by 200 with the `wait` field of `delegate` of item 1351 above (21,302 measured).
# Raised by 550 with `code_run` of item 1501 above (21,882 measured).
# The appearance tools add a single portable theme schema and two revision-only calls.
# Raised by 250 for the worktree, base commit and pull request arguments (item 1658), with 22,960 measured.
# Raised by 200 for `search_chats` of item 1740 above (23,208 measured, with the growth of main it was made on).
# Raised by 100 for `memory_save`'s `subject` of item 1729, a fact that changed replacing its older note (23,286
# measured).
# Raised by 100 for `show_page`'s words of item 1721 above (23,331 measured).
# Raised by 300 for `add_proof` of item 1853, a check that must exit 0 before the run is done (23,685 measured).
# Raised by 100 for the scope words of `recon` and `security_scan` (item 1887): 23,768 measured on the gate's runner.
TOOLS_BUDGET_TOKENS: int = 23_850


async def _first_parts(config: AppConfig) -> ContextParts:
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, await read_login_env(10.0, ()))
        manager: RunManager = RunManager(runtime)
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_FINISH]))
            run: RunRecord = await manager.start(
                "Write a short note about the weather in notes.txt.",
                "task",
                SCRIPTED_MODEL,
                None,
                "ui",
                None,
                None,
                "standard",
                None,
                "yolo",
                False,
                (),
            )
            async with asyncio.timeout(30.0):
                await manager.wait(run.id)
            told: list[EventRecord] = await runtime.store.events_of_kind(run.id, CONTEXT_PARTS_EVENT, 5)
        finally:
            await manager.shutdown()
            await close_runtime(runtime)
    assert len(told) == 1, "the first step's parts are recorded once a run"
    return ContextParts.model_validate_json(json.dumps(told[0].data))


def _with_context_size(config: AppConfig, size: int) -> AppConfig:
    """The same configuration, with this test's model given a chosen context size of ``size`` tokens
    (``[models.context]``), which is what a run's budget is cut to when the provider's window is larger."""
    models = config.models.model_copy(update={"context": {**config.models.context, SCRIPTED_MODEL: size}})
    return config.model_copy(update={"models": models})


async def test_a_tool_the_run_let_go_is_not_named_and_not_callable() -> None:
    """Item 1211: a run whose budget cannot hold every tool drops some, and its groups, registry and prompt are built
    again for what stays — so no section of the prompt opens for a tool it let go, and calling that tool answers
    unknown.
    A fit that runs after the prompt is built (or a registry built from the tools before the fit) turns this red."""
    # Every tool at every step (item 1280: groups are the default): `tools_offered` then counts every tool the run kept,
    # which is what the check below that fewer than all were offered rests on.
    full: AppConfig = eval_config(load_default_config())
    config: AppConfig = _with_context_size(
        full.model_copy(update={"agent": full.agent.model_copy(update={"tool_surface": "full"})}), 12_000
    )
    asked: ScriptedTurn = ScriptedTurn(
        text="", calls=(ScriptedCall(tool="show_page", arguments={"title": "x", "html": "<b>x</b>"}),)
    )
    parts: ContextParts
    answers: list[str]
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, await read_login_env(10.0, ()))
        manager: RunManager = RunManager(runtime)
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [asked, _FINISH]))
            run: RunRecord = await manager.start(
                "Write a short note about the weather in notes.txt.",
                "task",
                SCRIPTED_MODEL,
                None,
                "ui",
                None,
                None,
                "standard",
                None,
                "yolo",
                False,
                (),
            )
            async with asyncio.timeout(30.0):
                await manager.wait(run.id)
            told: list[EventRecord] = await runtime.store.events_of_kind(run.id, CONTEXT_PARTS_EVENT, 5)
            messages = await runtime.store.load_messages(run.thread_id, False)
        finally:
            await manager.shutdown()
            await close_runtime(runtime)
    parts = ContextParts.model_validate_json(json.dumps(told[0].data))
    answers = [message.content for message in messages if message.role == "tool"]
    assert parts.tools_offered < 82, "the budget left every tool in: this test measures a run that let some go"
    assert "Pages in the conversation" not in parts.system, sorted(parts.system)
    assert any("Unknown tool 'show_page'" in answer for answer in answers), answers[-3:]


async def test_a_fresh_homes_first_step_stays_within_its_budget() -> None:
    # The budget is that of every tool offered and the full prompt: the surface is `full` here (item 1280 made groups
    # the default, whose first step carries the core tools and the lines of the groups, and is smaller in all).
    config: AppConfig = eval_config(load_default_config())
    config = config.model_copy(update={"agent": config.agent.model_copy(update={"tool_surface": "full"})})
    parts: ContextParts = await _first_parts(config)
    system: int = round(sum(parts.system.values()) / 3.2)
    tools: int = round(sum(parts.tools.values()) / 3.2)
    said: str = (
        f"in all {parts.tokens}, system {system} ({parts.system}), tools {tools} in {parts.tools_offered} "
        f"({parts.tools}); raise the budget in this file saying why, or take back what grew"
    )
    assert parts.tokens <= BUDGET_TOKENS, said
    assert system <= SYSTEM_BUDGET_TOKENS, said
    assert tools <= TOOLS_BUDGET_TOKENS, said
    # The sections are the prompt's own headings, and the safety rules are among them.
    assert "Trust and safety" in parts.system and "core" in parts.tools
