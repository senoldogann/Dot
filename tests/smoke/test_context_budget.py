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
BUDGET_TOKENS: int = 23_800
# Raised by 100 for the line that has a run say what it does as it works, as a colleague would in a chat (item 830: the
# owner's screenshots of an app whose agents speak so, and the conversation without the steps; system 7,231 measured).
# Raised by 100 for the rules of item 851 above.
SYSTEM_BUDGET_TOKENS: int = 7_400
# Item 802: the delegate goal and tool description ask for a `Verification:` line; the old ceiling was 15,300.
# Raised by 100 for `todo_drop` (item 821): a run takes back a thing it put on Dot's own list by mistake, which one did
# by changing Dot's database instead (22,648 measured; tools 15,474).
# Raised by 300 for `ask_secret` (item 857) above, by 200 for `create_team` (item 864), and by 300 for `wait_until`
# (item 887) above, and by 100 for items 880 and 875 above.
TOOLS_BUDGET_TOKENS: int = 16_400


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


async def test_a_fresh_homes_first_step_stays_within_its_budget() -> None:
    parts: ContextParts = await _first_parts(eval_config(load_default_config()))
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
