"""In a chat Dot opened on its own initiative, a message, a post or a release waits for the owner, yolo too (item 850):
the owner's word of 2026-10-30 after Dot's initiative put a local build live and posted on their LinkedIn, both in
yolo. The gate on a real store and bus with no model: such a step asks the owner in every mode, never the review model,
an owner's rule that allows it does not answer for them, a refusal stands, and the same step outside such a chat is
waived in yolo as before. Then real runs on a scripted model: the initiative's run and the run of the owner's answer in
its chat both wait before a release, and a chat of the owner's own does it at once. The command is a push to main of a
folder that is no repository, which fails at once: nothing reaches a network."""

import asyncio
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.approvals import ApprovalBroker
from dotagent.agent.authorization import Authorization
from dotagent.agent.gate import RunGate, SiteLocks
from dotagent.agent.manager import RunManager
from dotagent.agent.review import ReviewRequest, ReviewVerdict
from dotagent.config import PaceConfig, load_config
from dotagent.core.errors import ApprovalRejected, PolicyDenied
from dotagent.core.paths import ensure_layout
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.events import EventBus
from dotagent.llm.router import parse_model_ref
from dotagent.policy.types import ActionInfo, ApprovalMode
from dotagent.runtime import Runtime, build_runtime, close_runtime
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
    summary="click f16e738 'Yayınla'",
    target="https://www.linkedin.com/sharing/compose",
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
RELEASE: ActionInfo = ActionInfo(
    tool="shell",
    kind="shell",
    risk="medium",
    summary="Run: npx wrangler pages deploy dist",
    target="",
    flags=("release",),
)
# A push to main in a folder that is no repository: git refuses it at once, before any network.
PUSH: str = "git -C /nonexistent/dot-item-850 push origin main"
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
        thread: ThreadRecord = await store.create_thread("initiative", "initiative")
        run: RunRecord = await store.create_run(
            thread.id,
            "On my own initiative: post the launch",
            "task",
            "ollama:none",
            None,
            "initiative",
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


def _gate(room: Room, approvals: ApprovalMode, owners_word: bool, mode: str = "task") -> RunGate:
    return RunGate(
        room.store,
        room.bus,
        room.broker,
        room.run.id,
        mode,  # type: ignore[arg-type]
        "aggressive",
        NO_PACE,
        {},
        approvals,
        _nobody,
        (),
        SiteLocks(),
        owners_word=owners_word,
    )


async def _pending(store: Store) -> ApprovalRecord:
    async with asyncio.timeout(WAIT_S):
        while True:
            waiting: list[ApprovalRecord] = await store.list_approvals("pending", 10)
            if waiting:
                return waiting[0]
            await asyncio.sleep(0.02)


@pytest.mark.parametrize("approvals", ["yolo", "auto", "ask", "supervised"])
@pytest.mark.parametrize("step", [POST, MAIL, RELEASE], ids=["post", "mail", "release"])
async def test_in_a_chat_of_the_initiative_the_step_asks_the_owner_in_every_mode(
    room: Room, approvals: ApprovalMode, step: ActionInfo
) -> None:
    gate: RunGate = _gate(room, approvals, owners_word=True)
    asked: asyncio.Task[Authorization] = asyncio.create_task(gate.authorize_recorded(step))
    question: ApprovalRecord = await _pending(room.store)
    assert question.tool == step.tool
    await room.broker.resolve(question.id, True, "test", "")
    async with asyncio.timeout(WAIT_S):
        done: Authorization = await asked
    assert done.decision.answered_by == "user"


async def test_the_owners_no_refuses_it_and_a_rule_that_allows_it_does_not_answer(room: Room) -> None:
    await room.store.add_rule("allow", "browser_act", ".*", "Let every click through")
    gate: RunGate = _gate(room, "yolo", owners_word=True)
    asked: asyncio.Task[Authorization] = asyncio.create_task(gate.authorize_recorded(POST))
    question: ApprovalRecord = await _pending(room.store)
    await room.broker.resolve(question.id, False, "test", "not this text")
    with pytest.raises(ApprovalRejected):
        async with asyncio.timeout(WAIT_S):
            await asked


@pytest.mark.parametrize("approvals", ["yolo", "auto"])
@pytest.mark.parametrize("case", ["handoff rule", "credential"])
async def test_a_hand_off_stays_the_owners_and_yolo_does_not_waive_it(
    room: Room, approvals: ApprovalMode, case: str
) -> None:
    """The independent review of item 850: a hand-off, stricter than the question, was left as it was, and yolo then
    waived it: an owner's rule that hands posts off, or a post that also carried a credential, went through unasked.
    It reaches the owner as a hand-off, in yolo too."""
    step: ActionInfo = POST
    if case == "handoff rule":
        await room.store.add_rule("handoff", "browser_act", ".*", "I post on LinkedIn myself")
    else:
        step = POST.model_copy(update={"flags": ("public_post", "credential")})
    gate: RunGate = _gate(room, approvals, owners_word=True)
    asked: asyncio.Task[Authorization] = asyncio.create_task(gate.authorize_recorded(step))
    question: ApprovalRecord = await _pending(room.store)
    assert question.severity == "handoff", question.severity
    await room.broker.resolve(question.id, False, "test", "")
    with pytest.raises(ApprovalRejected):
        async with asyncio.timeout(WAIT_S):
            await asked


async def test_a_refusal_stands(room: Room) -> None:
    """A research run stays read-only: its refusal is no question to turn into one."""
    gate: RunGate = _gate(room, "yolo", owners_word=True, mode="research")
    with pytest.raises(PolicyDenied):
        await gate.authorize_recorded(POST)
    assert await room.store.list_approvals("pending", 10) == []


async def test_outside_a_chat_of_the_initiative_yolo_waives_it_as_before(room: Room) -> None:
    gate: RunGate = _gate(room, "yolo", owners_word=False)
    for step in (POST, MAIL, RELEASE):
        done: Authorization = await gate.authorize_recorded(step)
        assert done.decision.answered_by in ("yolo", "policy"), step.summary
    assert await room.store.list_approvals("pending", 10) == []


# ----------------------------------------------------------------------------------------------------- real runs


def _push_then_finish() -> list[ScriptedTurn]:
    push = ScriptedTurn(text="", calls=(ScriptedCall(tool="shell", arguments={"command": PUSH}),))
    finish = ScriptedTurn(
        text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)
    )
    return [push, finish]


@pytest_asyncio.fixture
async def runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Runtime]:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    (home / "config.toml").write_text(
        '[browser]\nheadless = true\n\n[agent]\nlessons = "off"\nplaybooks = false\n\n'
        f'[models]\nmain = "{SCRIPTED_MODEL}"'
        "\n\n[memory]\nembed_enabled = false\n",
        encoding="utf-8",
    )
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _push_then_finish() * 3))
    try:
        yield built
    finally:
        await close_runtime(built)


async def _refused_pending(runtime: Runtime) -> ApprovalRecord:
    question: ApprovalRecord = await _pending(runtime.store)
    await runtime.approvals.resolve(question.id, False, "test", "")
    return question


async def test_the_initiatives_run_and_the_run_of_the_owners_answer_wait_and_the_owners_own_chat_does_not(
    runtime: Runtime,
) -> None:
    manager: RunManager = RunManager(runtime)
    try:
        chat: ThreadRecord = await runtime.store.create_thread("On my own initiative: launch", "initiative")
        own: RunRecord = await manager.start_own("On my own initiative: release", "Release it.", "task", chat.id, False)
        assert own.approvals == "yolo"
        first: ApprovalRecord = await _refused_pending(runtime)
        assert (first.run_id, first.tool) == (own.id, "shell")
        async with asyncio.timeout(RUN_WAIT_S):
            await manager.wait(own.id)
        # The owner answers its question in the same chat: the run that answer starts is the owner's, in yolo, and
        # still asks before the release (the run that posted on 2026-10-30 was such a one).
        answer: RunRecord = await manager.start(
            "Ben yayınlayayım", "task", SCRIPTED_MODEL, chat.id, "ui", None, None, "standard", None, "yolo", False, ()
        )
        second: ApprovalRecord = await _refused_pending(runtime)
        assert second.run_id == answer.id
        async with asyncio.timeout(RUN_WAIT_S):
            await manager.wait(answer.id)
        # A chat of the owner's own, in yolo, does it at once: no question.
        started: float = time.time()
        mine: RunRecord = await manager.start(
            "Push it", "task", SCRIPTED_MODEL, None, "ui", None, None, "standard", None, "yolo", False, ()
        )
        async with asyncio.timeout(RUN_WAIT_S):
            assert (await manager.wait(mine.id)).status == "done"
        asked_after: list[ApprovalRecord] = [
            record for record in await runtime.store.list_approvals(None, 20) if record.created_at >= started
        ]
        assert [record.status for record in asked_after if record.run_id == mine.id] in ([], ["approved"])
        assert all(record.decided_by != "test" for record in asked_after if record.run_id == mine.id)
    finally:
        await manager.shutdown()
