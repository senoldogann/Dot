"""A reply that came to a mail a chat sent goes back to that chat (item 926). On a real store and bus: the gate keeps
the addresses a message goes to by its chat, the tool finds the chat by a reply's sender and starts a run there,
untrusted as the run that found it, hands the same sender once in a while, and leaves to the finder a reply no other
chat wrote for; the manager's run of such a reply is one nobody watches, in the chat's own mode and model. No model is
called and nothing leaves the Mac."""

import asyncio
import time
from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
import pytest_asyncio

from dotagent.agent.approvals import ApprovalBroker
from dotagent.agent.gate import RunGate, SiteLocks
from dotagent.agent.manager import RunManager
from dotagent.agent.review import ReviewRequest, ReviewVerdict
from dotagent.config import PaceConfig, load_config
from dotagent.core.errors import RunConflict
from dotagent.core.paths import ensure_layout
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.events import EventBus
from dotagent.llm.router import parse_model_ref
from dotagent.policy.types import ActionInfo
from dotagent.proactive import INITIATIVE_SOURCE, REPLY_SOURCE
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.store.db import Store
from dotagent.store.models import Correspondent, RunRecord, ThreadRecord
from dotagent.tools.base import ToolResult
from dotagent.tools.context import ToolContext
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.replies import PASS_REPLY_TOOL, build_reply_tools

pytestmark = pytest.mark.asyncio

NO_PACE: PaceConfig = PaceConfig(
    public_per_hour=0, public_per_day=0, social_per_hour=0, social_per_day=0, public_spacing_s=0.0
)
SEND: ActionInfo = ActionInfo(
    tool="browser_act",
    kind="browser_click",
    risk="medium",
    summary="click f6e356 'Send'",
    target="https://webmail.zone.fi/u/0/compose",
    flags=("external_send", "message_send"),
)
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider


async def _nobody(request: ReviewRequest) -> ReviewVerdict:
    raise AssertionError(f"the review model must not be asked about this: {request.action.summary}")


async def _run(store: Store, thread_id: str, goal: str, source: str) -> RunRecord:
    return await store.create_run(
        thread_id,
        goal,
        "task",
        "ollama:none",
        None,
        source,
        "medium",
        "standard",
        None,
        "yolo",
        False,
        None,
        None,
        None,
    )


async def test_the_addresses_a_message_goes_to_are_kept_by_its_chat(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        bus: EventBus = EventBus(store)
        thread: ThreadRecord = await store.create_thread("Offer to Acme", "ui")
        run: RunRecord = await _run(store, thread.id, "Write the offer to Acme and send it", "ui")
        # The run searched the inbox for a sender, typed the address into the field To, then clicks Send.
        await store.add_event(
            run.id,
            "tool_call",
            {"tool": "browser_act", "args": {"steps": [{"fill": "Search", "text": "from:news@acme.fi"}]}},
        )
        await store.add_event(
            run.id, "tool_call", {"tool": "browser_act", "args": {"steps": [{"fill": "To", "text": "Sales@Acme.fi"}]}}
        )
        gate: RunGate = RunGate(
            store, bus, ApprovalBroker(store, bus, 5.0), run.id, "task", "aggressive", NO_PACE, {}, "yolo", _nobody,
            (), SiteLocks(), chat_id=thread.id,
        )  # fmt: skip
        await gate.authorize_recorded(SEND)
        found: Correspondent | None = await store.chat_that_wrote_to("sales@acme.fi", "thr_none", 0.0)
        assert found is not None and (found.thread_id, found.run_id) == (thread.id, run.id)
        assert await store.chat_that_wrote_to("nobody@acme.fi", "thr_none", 0.0) is None
        # An address only searched for is no correspondent of the chat.
        assert await store.chat_that_wrote_to("news@acme.fi", "thr_none", 0.0) is None
    finally:
        await store.close()


class _Spawner:
    def __init__(self, busy: bool) -> None:
        self.started: list[tuple[str, str]] = []
        self.busy: bool = busy

    async def hand_reply(self, from_run_id: str, thread_id: str, goal: str, opening: str) -> RunRecord:
        if self.busy:
            raise RunConflict("a run works in that chat now; hand the reply later", {"thread_id": thread_id})
        self.started.append((thread_id, opening))
        return cast(RunRecord, SimpleNamespace(id="run_mvz1a2b3_aaaaaa"))


async def _pass(store: Store, thread_id: str, spawner: _Spawner, sender: str) -> ToolResult:
    ctx: ToolContext = cast(
        ToolContext,
        SimpleNamespace(
            runtime=SimpleNamespace(store=store),
            run_id="run_mvz1a2b3_cccccc",
            thread_id=thread_id,
            spawner=spawner,
            ever_tainted=lambda: True,
        ),
    )
    prepared = await build_reply_tools()[0].prepare(
        {"sender": sender, "what": "In the inbox, 'Re: Offer', today: they accept and ask for the invoice."}, ctx
    )
    return await prepared.run(ctx)


async def test_a_reply_goes_to_the_chat_that_wrote_once_and_any_other_stays_with_the_finder(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        offer: ThreadRecord = await store.create_thread("Offer to Acme", "ui")
        mail: ThreadRecord = await store.create_thread("Client mail", "schedule")
        await store.note_correspondents(["sales@acme.fi"], offer.id, "run_mvz1a2b3_bbbbbb", 1.0e10 - 3600.0)
        # The finder's own chat named the address later, in passing (the third pre-push review): the reply still goes to
        # the chat that wrote to it.
        await store.note_correspondents(["sales@acme.fi"], mail.id, "run_mvz1a2b3_dddddd", 1.0e10 - 60.0)
        busy: _Spawner = _Spawner(busy=True)
        assert "hand the reply again later" in (await _pass(store, mail.id, busy, "sales@acme.fi")).text
        spawner: _Spawner = _Spawner(busy=False)
        handed: ToolResult = await _pass(store, mail.id, spawner, "Sales@Acme.fi")
        assert f"Handed to the chat {offer.id}" in handed.text
        [(thread_id, opening)] = spawner.started
        assert thread_id == offer.id and "they accept and ask for the invoice" in opening
        # The same sender a moment later: handed already, not a second run.
        assert "handed to its chat" in (await _pass(store, mail.id, spawner, "sales@acme.fi")).text
        assert len(spawner.started) == 1
        # A sender no other chat wrote to, and a reply to the finder's own chat, stay with the finder.
        assert "deal with the reply here" in (await _pass(store, mail.id, spawner, "news@example.org")).text
        await store.note_correspondents(["buyer@acme.fi"], offer.id, "run_mvz1a2b3_bbbbbb", 1.0e10 - 3600.0)
        assert "deal with the reply here" in (await _pass(store, offer.id, spawner, "buyer@acme.fi")).text
    finally:
        await store.close()


_HAND: ScriptedTurn = ScriptedTurn(
    text="",
    calls=(
        ScriptedCall(
            tool=PASS_REPLY_TOOL,
            arguments={"sender": "sales@acme.fi", "what": "In the inbox, 'Re: Offer': they accept."},
        ),
    ),
)
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)
)


@pytest_asyncio.fixture
async def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Runtime]:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    (home / "config.toml").write_text(
        '[browser]\nheadless = true\nlessons = "off"\nplaybooks = false\n\n'
        f'[models]\nmain = "{SCRIPTED_MODEL}"\n\n[memory]\nembed_enabled = false\n\n[agent]\ngoal_mode = false',
        encoding="utf-8",
    )
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)


async def _hand_from(runtime: Runtime, manager: RunManager, source: str) -> tuple[RunRecord, ThreadRecord]:
    """A run of ``source`` that finds a reply of sales@acme.fi and calls ``pass_reply``, where the owner's chat "Offer
    to Acme" (yolo) wrote to that address."""
    offer: ThreadRecord = await runtime.store.create_thread("Offer to Acme", "ui")
    sent: RunRecord = await _run(runtime.store, offer.id, "Send the offer to sales@acme.fi", "ui")
    await runtime.store.finish_run(sent.id, "done", "Sent.")
    await runtime.store.note_correspondents(["sales@acme.fi"], offer.id, sent.id, time.time())
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, [_HAND, _FINISH, _FINISH, _FINISH]))
    finder: RunRecord = await manager.start(
        "Read the owner's mail", "task", SCRIPTED_MODEL, None, source, None, None, "standard", None, "yolo", False, ()
    )
    async with asyncio.timeout(60):
        await manager.wait(finder.id)
    return finder, offer


async def test_a_schedules_run_hands_a_reply_to_the_owners_chat_as_one_nobody_watches(runtime: Runtime) -> None:
    manager: RunManager = RunManager(runtime)
    try:
        finder, offer = await _hand_from(runtime, manager, SCHEDULE_SOURCE)
        [reply] = [run for run in await runtime.store.list_runs(5, offer.id) if run.source == REPLY_SOURCE]
        assert (reply.thread_id, reply.approvals) == (offer.id, "yolo")
        async with asyncio.timeout(60):
            await manager.wait(reply.id)
        # Untrusted, though the run that found it read nothing marked so (the third pre-push review: a command's output
        # marks nothing): a third party's mail set it off.
        assert not await runtime.store.run_ever_tainted(finder.id)
        assert await runtime.store.run_ever_tainted(reply.id)
    finally:
        await manager.shutdown()


async def test_a_run_of_dots_own_initiative_hands_no_reply(runtime: Runtime) -> None:
    """The pre-push review of item 926: the run a reply starts in the owner's chat sends as that chat's, judged by the
    review model in yolo, while a run of Dot's own initiative asks the owner before any message (item 850). Handed from
    one, the reply would be sent with nobody asked; such a run keeps the reply, refused by the code."""
    manager: RunManager = RunManager(runtime)
    try:
        finder, offer = await _hand_from(runtime, manager, INITIATIVE_SOURCE)
        assert [run for run in await runtime.store.list_runs(5, offer.id) if run.source == REPLY_SOURCE] == []
        [result] = [
            event
            for event in await runtime.store.events_of_kinds(finder.id, ("tool_result",), 20)
            if event.data.get("tool") == PASS_REPLY_TOOL
        ]
        assert result.data["ok"] is False and "The reply stays with this run" in str(result.data["text"])
    finally:
        await manager.shutdown()
