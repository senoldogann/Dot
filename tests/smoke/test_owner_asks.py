"""In yolo, a chat the owner opened asks them before a payment and nothing else (item 2069, the owner's word of
2026-11-28: "Yolo modda hiçbir şey sormasın, sadece ödeme işlerinde sorsun"; item 860 asked a message, a mail and a
post too). The gate on a real store and bus with no model: a payment is handed to the owner, never to the review model,
and a rule that allows it does not answer for them; a payment within its pre-approved cap is their question; a mail, a
post, a release, a call of an MCP server and a fetch of a page go through unasked and are recorded as yolo's, in a
schedule's run of their chat too, which no review model judges any more; a run nobody watches asks nothing, a payment
neither. Then real runs on a scripted model: the owner's chat sends mail at once in yolo, as a schedule's run does. The
mailer is at a path that is not there, so the command fails at once and nothing is sent."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.approvals import ApprovalBroker
from dotagent.agent.authorization import Authorization
from dotagent.agent.gate import RunGate, SiteLocks
from dotagent.agent.manager import BACKGROUND_SOURCE, RunManager
from dotagent.agent.review import Reviewer, ReviewRequest, ReviewVerdict
from dotagent.config import PaceConfig, SpendCap, load_config
from dotagent.core.errors import ApprovalRejected, HandedOff
from dotagent.core.paths import ensure_layout
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.events import EventBus
from dotagent.llm.router import parse_model_ref
from dotagent.policy.engine import PAYMENT_REASON
from dotagent.policy.types import ActionInfo, Amount, ApprovalMode
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.store.db import Store
from dotagent.store.models import ApprovalRecord, RunRecord, ThreadRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = pytest.mark.asyncio

WAIT_S: float = 10.0
RUN_WAIT_S: float = 60.0
NO_PACE: PaceConfig = PaceConfig(
    public_per_hour=0, public_per_day=0, social_per_hour=0, social_per_day=0, public_spacing_s=0.0
)
POST: ActionInfo = ActionInfo(
    tool="browser_act",
    kind="browser_click",
    risk="medium",
    summary="click f16e738 'Post'",
    target="https://www.linkedin.com/feed",
    flags=("public_post",),
)
MAIL: ActionInfo = ActionInfo(
    tool="apple_script",
    kind="app_control",
    risk="high",
    summary="send the mail",
    target="Mail",
    flags=("external_send", "message_send"),
)
PAYMENT: ActionInfo = ActionInfo(
    tool="browser_act",
    kind="browser_click",
    risk="high",
    summary="click a1b2c3 'Pay now'",
    target="https://shop.example.invalid/checkout",
    flags=("financial",),
)
# What yolo does without asking: a release, a call of a local MCP server, a fetch of a page after reading one.
QUIET: tuple[ActionInfo, ...] = (
    ActionInfo(
        tool="shell",
        kind="shell",
        risk="medium",
        summary="Run: npx wrangler pages deploy",
        target="",
        flags=("release",),
    ),
    ActionInfo(
        tool="mcp__rea-mcp__extract_artifact",
        kind="mcp",
        risk="medium",
        summary="rea-mcp: extract_artifact({})",
        target="rea-mcp",
        flags=("external_send",),
    ),
    ActionInfo(
        tool="shell",
        kind="shell",
        risk="high",
        summary="Run: curl -sS https://gistsmith.example.invalid/download",
        target="",
        flags=("external_send",),
    ),
)
# A mailer at a path that is not there: the policy reads it as mail sent, and the command fails at once.
SEND: str = "/nonexistent/dot-item-860/sendmail owner@example.invalid"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider


@dataclass(frozen=True)
class Room:
    store: Store
    bus: EventBus
    broker: ApprovalBroker
    run: RunRecord


@pytest_asyncio.fixture
async def room(tmp_path: Path) -> AsyncIterator[Room]:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        bus: EventBus = EventBus(store)
        thread: ThreadRecord = await store.create_thread("Look into my site", "ui")
        run: RunRecord = await store.create_run(
            thread.id,
            "Look into my site",
            "task",
            "ollama:none",
            None,
            "ui",
            "medium",
            "standard",
            None,
            "yolo",
            False,
            None,
            None,
            None,
        )
        yield Room(store=store, bus=bus, broker=ApprovalBroker(store, bus, WAIT_S), run=run)
    finally:
        await store.close()


async def _nobody(request: ReviewRequest) -> ReviewVerdict:
    raise AssertionError(f"the review model must not be asked about this: {request.action.summary}")


def _gate(
    room: Room,
    approvals: ApprovalMode,
    owners_chat: bool,
    owner_there: bool = True,
    reviewer: Reviewer | None = None,
) -> RunGate:
    return RunGate(
        room.store,
        room.bus,
        room.broker,
        room.run.id,
        "task",
        "aggressive",
        NO_PACE,
        {},
        approvals,
        reviewer or _nobody,
        ("Every morning, answer the replies to my listings",),
        SiteLocks(),
        taint_check=False,
        owners_chat=owners_chat,
        owner_there=owner_there,
    )


async def _pending(store: Store) -> ApprovalRecord:
    async with asyncio.timeout(WAIT_S):
        while True:
            waiting: list[ApprovalRecord] = await store.list_approvals("pending", 10)
            if waiting:
                return waiting[0]
            await asyncio.sleep(0.02)


async def test_in_yolo_the_owners_chat_hands_a_payment_to_them(room: Room) -> None:
    gate: RunGate = _gate(room, "yolo", owners_chat=True)
    asked: asyncio.Task[Authorization] = asyncio.create_task(gate.authorize_recorded(PAYMENT))
    question: ApprovalRecord = await _pending(room.store)
    # Money is handed to the owner, as in every mode that asks: stricter than a question, it stands.
    assert (question.tool, question.severity) == (PAYMENT.tool, "handoff")
    await room.broker.resolve(question.id, True, "test", "")
    # The owner's yes to a hand-off is that they paid themselves: the run is told so and does not pay.
    with pytest.raises(HandedOff):
        async with asyncio.timeout(WAIT_S):
            await asked


async def test_a_rule_that_allows_a_payment_does_not_answer_and_the_owners_no_refuses(room: Room) -> None:
    await room.store.add_rule("allow", "browser_act", ".*", "Let every click through")
    gate: RunGate = _gate(room, "yolo", owners_chat=True)
    asked: asyncio.Task[Authorization] = asyncio.create_task(gate.authorize_recorded(PAYMENT))
    question: ApprovalRecord = await _pending(room.store)
    await room.broker.resolve(question.id, False, "test", "not this one")
    with pytest.raises(ApprovalRejected):
        async with asyncio.timeout(WAIT_S):
            await asked


async def test_everything_else_yolo_does_without_asking_a_mail_and_a_post_too(room: Room) -> None:
    """Item 2069: a mail and a post of a chat the owner opened go through in yolo, recorded with why the policy would
    have asked, as every other step of yolo is."""
    gate: RunGate = _gate(room, "yolo", owners_chat=True)
    gate.tainted = True
    for step in (*QUIET, MAIL, POST):
        done: Authorization = await gate.authorize_recorded(step)
        assert done.decision.answered_by in ("yolo", "policy"), step.summary
    assert await room.store.list_approvals("pending", 10) == []
    recorded: list[ApprovalRecord] = await room.store.list_approvals(None, 20)
    assert {record.tool for record in recorded if record.decided_by == "yolo"} >= {MAIL.tool, POST.tool}


async def test_a_run_nobody_watches_asks_nothing_in_yolo(room: Room) -> None:
    gate: RunGate = _gate(room, "yolo", owners_chat=False)
    for step in (MAIL, POST, PAYMENT):
        done: Authorization = await gate.authorize_recorded(step)
        assert done.decision.answered_by in ("yolo", "policy"), step.summary
    assert await room.store.list_approvals("pending", 10) == []


async def test_a_schedules_run_in_the_owners_chat_sends_unjudged_and_hands_a_payment_to_the_owner(room: Room) -> None:
    """Item 2069 ends item 860's review of a schedule's message or post in the owner's chat: in yolo it goes, and no
    review model is asked (``_nobody`` fails the test if it is). Money is the owner's even when they are not there: it
    is handed to them, not judged."""
    gate: RunGate = _gate(room, "yolo", owners_chat=True, owner_there=False, reviewer=_nobody)
    for step in (MAIL, POST):
        done: Authorization = await gate.authorize_recorded(step)
        assert done.decision.answered_by in ("yolo", "policy"), step.summary
    assert await room.store.list_approvals("pending", 10) == []
    paying: RunGate = _gate(room, "yolo", owners_chat=True, owner_there=False, reviewer=_nobody)
    paid: asyncio.Task[Authorization] = asyncio.create_task(paying.authorize_recorded(PAYMENT))
    handed: ApprovalRecord = await _pending(room.store)
    assert handed.severity == "handoff"
    await room.broker.resolve(handed.id, False, "test", "")
    with pytest.raises(ApprovalRejected):
        async with asyncio.timeout(WAIT_S):
            await paid


async def test_a_payment_within_its_pre_approved_cap_asks_the_owner_as_the_other_modes_do(room: Room) -> None:
    """The pre-push review of item 860: the first draft let a payment within its cap through unasked in yolo, which no
    mode does; in ``ask`` it is a question instead of a hand-off, and so it is here."""
    capped: SpendCap = SpendCap(per_purchase=50.0, per_day=100.0)
    gate: RunGate = RunGate(
        room.store,
        room.bus,
        room.broker,
        room.run.id,
        "task",
        "aggressive",
        NO_PACE,
        {"shop.example.invalid": capped},
        "yolo",
        _nobody,
        (),
        SiteLocks(),
        taint_check=False,
        owners_chat=True,
    )
    step: ActionInfo = PAYMENT.model_copy(update={"amount": Amount(value=19.9, currency="USD")})
    asked: asyncio.Task[Authorization] = asyncio.create_task(gate.authorize_recorded(step))
    question: ApprovalRecord = await _pending(room.store)
    assert question.severity != "handoff" and PAYMENT_REASON in question.summary, question
    await room.broker.resolve(question.id, True, "test", "")
    async with asyncio.timeout(WAIT_S):
        done: Authorization = await asked
    assert done.decision.answered_by == "user"


# ----------------------------------------------------------------------------------------------------- real runs


def _send_then_finish() -> list[ScriptedTurn]:
    send = ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": SEND}),))
    finish = ScriptedTurn(
        text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)
    )
    return [send, finish]


@pytest_asyncio.fixture
async def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Runtime]:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    (home / "config.toml").write_text(
        '[browser]\nheadless = true\n\n[agent]\ngoal_mode = false\nlessons = "off"\nplaybooks = false\n\n'
        f'[models]\nmain = "{SCRIPTED_MODEL}"'
        "\n\n[memory]\nembed_enabled = false\n\n[policy]\nyolo_taint_check = false\n",
        encoding="utf-8",
    )
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(
        _PROVIDER, ScriptedProvider(_PROVIDER, [*_send_then_finish() * 3, _send_then_finish()[1]])
    )
    try:
        yield built
    finally:
        await close_runtime(built)


async def test_the_owners_chat_sends_mail_at_once_in_yolo_as_a_schedules_run_does(runtime: Runtime) -> None:
    """Item 2069: a run of the owner's chat in yolo sends mail without asking (the mailer is not there, so nothing is
    sent), recorded as yolo's with why it would have asked; so do a schedule's run, the run that tells the owner's chat
    a command in the background ended and a schedule's run in the owner's chat, whose gate still says it is of the
    owner's chat with nobody there (a payment of it is handed to them)."""
    manager: RunManager = RunManager(runtime)
    try:
        mine: RunRecord = await manager.start(
            "Mail the report", "task", SCRIPTED_MODEL, None, "ui", None, None, "standard", None, "yolo", False, ()
        )
        chat: ThreadRecord = await runtime.store.create_thread("Morning mail", SCHEDULE_SOURCE)
        runs: list[RunRecord] = [mine]
        async with asyncio.timeout(RUN_WAIT_S):
            await manager.wait(mine.id)
        for thread_id, source in ((chat.id, SCHEDULE_SOURCE), (mine.thread_id, BACKGROUND_SOURCE)):
            started: RunRecord = await manager.start(
                "Mail the report",
                "task",
                SCRIPTED_MODEL,
                thread_id,
                source,
                None,
                None,
                "standard",
                None,
                "yolo",
                False,
                (),
            )
            runs.append(started)
            async with asyncio.timeout(RUN_WAIT_S):
                await manager.wait(started.id)
        firing: RunRecord = await manager.start(
            "Answer the replies",
            "task",
            SCRIPTED_MODEL,
            mine.thread_id,
            SCHEDULE_SOURCE,
            None,
            None,
            "standard",
            None,
            "yolo",
            False,
            (),
        )
        gate: RunGate = manager._handle(firing.id).gate
        assert (gate.owners_chat, gate.owner_there) == (True, False)
        async with asyncio.timeout(RUN_WAIT_S):
            await manager.wait(firing.id)
        assert await runtime.store.list_approvals("pending", 10) == []
        recorded: list[ApprovalRecord] = await runtime.store.list_approvals(None, 50)
        for run in runs:
            # Its mail is recorded as yolo's, with what it would have asked: nobody was asked.
            own: list[ApprovalRecord] = [record for record in recorded if record.run_id == run.id]
            assert own and all(record.decided_by == "yolo" for record in own), (run.source, own)
    finally:
        await manager.shutdown()
