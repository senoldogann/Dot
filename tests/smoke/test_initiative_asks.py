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
from dotagent.agent.parking import is_kept_send, send_parking
from dotagent.agent.review import ReviewRequest, ReviewVerdict
from dotagent.config import PaceConfig, load_config
from dotagent.core.errors import ApprovalParked, ApprovalRejected, PolicyDenied
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


def _gate(
    room: Room,
    approvals: ApprovalMode,
    owners_word: bool,
    mode: str = "task",
    owner_there: bool = True,
    locks: SiteLocks | None = None,
) -> RunGate:
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
        SiteLocks() if locks is None else locks,
        owners_word=owners_word,
        owner_there=owner_there,
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


@pytest.mark.parametrize("step", [POST, MAIL, RELEASE], ids=["post", "mail", "release"])
async def test_a_run_nobody_is_there_for_keeps_the_step_and_the_owners_yes_lets_that_step_through_once(
    room: Room, step: ActionInfo
) -> None:
    """Item 922: the run of Dot's own initiative does not wait for the owner (it waited 15 minutes and failed): the
    step is kept as a card for 30 days, the run told to keep its draft, and the same step again is that card. The run
    their yes reaches does that step exactly as the card showed it, once; a step of a page, whose references are new
    in that run, asks them once more; the next one asks too."""
    alone: RunGate = _gate(room, "yolo", owners_word=True, owner_there=False)
    alone.keep_sends_for(send_parking(room.run.thread_id, None, None))
    with pytest.raises(ApprovalParked, match="Keep what you wrote as a draft"):
        await alone.authorize_recorded(step)
    [kept] = await room.store.list_approvals("left", 10)
    assert kept.tool == step.tool and kept.kept_until is not None and kept.digest is not None
    assert kept.kept_until - time.time() > 29 * 24 * 3600, "kept for 30 days, not for parked_hours"
    assert is_kept_send(kept.args)
    exact: bool = kept.args["exact"] is True
    assert exact == (step is not POST), "a step of a page is never met again as it was"
    with pytest.raises(ApprovalParked, match="waits for the user's yes already"):
        await alone.authorize_recorded(step)
    assert len(await room.store.list_approvals("left", 10)) == 1
    carried: RunGate = _gate(room, "yolo", owners_word=True)
    if exact:
        carried.allow_send_once(kept.digest)
        done: Authorization = await carried.authorize_recorded(step)
        assert done.decision.answered_by == "user"
    asked: asyncio.Task[Authorization] = asyncio.create_task(carried.authorize_recorded(step))
    question: ApprovalRecord = await _pending(room.store)
    await room.broker.resolve(question.id, False, "test", "")
    with pytest.raises(ApprovalRejected):
        async with asyncio.timeout(WAIT_S):
            await asked


@pytest.mark.parametrize(
    "other",
    [
        MAIL.model_copy(update={"summary": "send the mail to someone else"}),
        MAIL.model_copy(update={"flags": ("external_send", "message_send", "privilege")}),
    ],
    ids=["another mail", "a flag more"],
)
async def test_the_yes_lets_through_no_other_send_and_no_other_run(room: Room, other: ActionInfo) -> None:
    """The independent and the pre-push review of item 922: a yes was a permit any run of the chat could spend on any
    send of the kind. It is the card's step exactly, in the run it reached: another mail, or the same with a flag more,
    asks; a run it did not reach asks for the card's step itself."""
    alone: RunGate = _gate(room, "yolo", owners_word=True, owner_there=False)
    alone.keep_sends_for(send_parking(room.run.thread_id, None, None))
    with pytest.raises(ApprovalParked):
        await alone.authorize_recorded(MAIL)
    [kept] = await room.store.list_approvals("left", 10)
    assert kept.digest is not None
    reached: RunGate = _gate(room, "yolo", owners_word=True)
    reached.allow_send_once(kept.digest)
    elsewhere: RunGate = _gate(room, "yolo", owners_word=True)
    for gate, step in ((reached, other), (elsewhere, MAIL)):
        asked: asyncio.Task[Authorization] = asyncio.create_task(gate.authorize_recorded(step))
        question: ApprovalRecord = await _pending(room.store)
        assert question.summary.startswith(step.summary), question.summary
        await room.broker.resolve(question.id, False, "test", "")
        with pytest.raises(ApprovalRejected):
            async with asyncio.timeout(WAIT_S):
                await asked


async def test_a_yes_that_reached_the_run_at_work_has_it_ask_the_owner_rather_than_keep_another_card(
    room: Room,
) -> None:
    """The pre-push review of item 922: the owner's yes reached the run that kept a post while it still worked; the post
    on a page it opens anew is not the card's step, and it kept a new card on every yes. Now it asks them, and waits."""
    alone: RunGate = _gate(room, "yolo", owners_word=True, owner_there=False)
    alone.keep_sends_for(send_parking(room.run.thread_id, None, None))
    with pytest.raises(ApprovalParked):
        await alone.authorize_recorded(POST)
    alone.owner_answers_sends()
    asked: asyncio.Task[Authorization] = asyncio.create_task(alone.authorize_recorded(POST))
    question: ApprovalRecord = await _pending(room.store)
    await room.broker.resolve(question.id, True, "test", "")
    async with asyncio.timeout(WAIT_S):
        assert (await asked).decision.answered_by == "user"
    assert len(await room.store.list_approvals("left", 10)) == 1


async def test_a_terminals_line_is_never_done_on_a_yes_given_to_its_card(room: Room) -> None:
    """The independent review of item 922: a terminal's card names its session by a number a restart gives again to
    another folder or program, so its step is not the card's whole step: its yes lets nothing through."""
    typed: ActionInfo = ActionInfo(
        tool="terminal_send",
        kind="shell",
        risk="medium",
        summary="Type into t3: npx wrangler pages deploy dist",
        target="npx wrangler pages deploy dist",
        flags=("release",),
    )
    alone: RunGate = _gate(room, "yolo", owners_word=True, owner_there=False)
    alone.keep_sends_for(send_parking(room.run.thread_id, None, None))
    with pytest.raises(ApprovalParked):
        await alone.authorize_recorded(typed)
    [kept] = await room.store.list_approvals("left", 10)
    assert kept.args["exact"] is False


async def test_a_question_that_waits_for_the_owner_holds_no_other_run_on_its_site(room: Room) -> None:
    """The pre-push review of item 922: a question may wait up to `approval_timeout_s`, and it was asked under its
    site's pace lock, so every other run's step on that site waited with it. It is asked outside the lock."""
    shared: SiteLocks = SiteLocks()
    asking: RunGate = _gate(room, "ask", owners_word=False, locks=shared)
    asked: asyncio.Task[Authorization] = asyncio.create_task(asking.authorize_recorded(POST))
    question: ApprovalRecord = await _pending(room.store)
    other: RunGate = _gate(room, "yolo", owners_word=False, locks=shared)
    async with asyncio.timeout(WAIT_S):
        assert (await other.authorize_recorded(POST)).decision.answered_by == "yolo"
    await room.broker.resolve(question.id, True, "test", "")
    async with asyncio.timeout(WAIT_S):
        assert (await asked).decision.answered_by == "user"


# A release asks nothing outside a chat of Dot's own initiative (item 850): the owner's chat asks a mail and a post.
@pytest.mark.parametrize("step", [POST, MAIL], ids=["post", "mail"])
async def test_a_send_nobody_answers_in_time_is_kept_with_its_draft_in_any_chat(room: Room, step: ActionInfo) -> None:
    """Item 922 (the owner: "Dot bizden cevap beklerken zaman aşımına uğrayıp error veriyor"): a message, a post or a
    release of the owner's own chat whose question nobody answers in time is kept as a card for their yes, not
    refused; any other step is refused as before."""
    short: ApprovalBroker = ApprovalBroker(room.store, room.bus, 0.3)
    gate: RunGate = RunGate(
        room.store, room.bus, short, room.run.id, "task", "aggressive", NO_PACE, {}, "ask", _nobody, (), SiteLocks()
    )
    gate.keep_sends_for(send_parking(room.run.thread_id, None, None))
    with pytest.raises(ApprovalParked, match="Keep what you wrote as a draft"):
        async with asyncio.timeout(WAIT_S):
            await gate.authorize_recorded(step)
    [kept] = await room.store.list_approvals("left", 10)
    assert kept.tool == step.tool and is_kept_send(kept.args)


async def test_a_send_of_a_run_that_read_outside_content_is_kept_but_its_yes_asks_again(room: Room) -> None:
    """The pre-push review of item 922: a send of a tainted run (its question raised by what it read, then taken over by
    the initiative's guard, whose source hid it) was kept as an exact card, which a later yes did unasked; item 483
    never lets a later yes answer such a question by itself. It is kept, its draft with it, but never exact, and a
    tainted run the yes reaches asks for the step itself."""
    gate: RunGate = _gate(room, "ask", owners_word=True, owner_there=False)
    gate.keep_sends_for(send_parking(room.run.thread_id, None, None))
    gate.taint()
    with pytest.raises(ApprovalParked):
        await gate.authorize_recorded(MAIL)
    [kept] = await room.store.list_approvals("left", 10)
    assert kept.args["exact"] is False and kept.digest is not None
    carried: RunGate = _gate(room, "ask", owners_word=True)
    carried.taint()
    carried.allow_send_once(kept.digest)
    asked: asyncio.Task[Authorization] = asyncio.create_task(carried.authorize_recorded(MAIL))
    question: ApprovalRecord = await _pending(room.store)
    await room.broker.resolve(question.id, True, "test", "")
    async with asyncio.timeout(WAIT_S):
        assert (await asked).decision.answered_by == "user"


async def test_in_free_mode_nothing_is_asked_and_off_again_it_is(room: Room) -> None:
    """Item 922: the owner's free mode asks nothing, these steps neither, from the next step on."""
    gate: RunGate = _gate(room, "ask", owners_word=True)
    room.broker.switch_free(True)
    for step in (POST, MAIL, RELEASE):
        done: Authorization = await gate.authorize_recorded(step)
        assert done.decision.answered_by in ("yolo", "policy"), step.summary
    assert await room.store.list_approvals("pending", 10) == []
    room.broker.switch_free(False)
    asked: asyncio.Task[Authorization] = asyncio.create_task(gate.authorize_recorded(POST))
    question: ApprovalRecord = await _pending(room.store)
    await room.broker.resolve(question.id, True, "test", "")
    async with asyncio.timeout(WAIT_S):
        assert (await asked).decision.answered_by == "user"


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
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, _push_then_finish() * 4))
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
        # Nobody is there for it: the release is kept as a card, not waited for (item 922), and the run ends.
        async with asyncio.timeout(RUN_WAIT_S):
            await manager.wait(own.id)
        [first] = await runtime.store.list_approvals("left", 10)
        assert (first.run_id, first.tool) == (own.id, "shell")
        assert await runtime.store.list_approvals("pending", 10) == []
        # The owner's yes carries it on in the chat: the run it starts does the release once, asking nothing.
        await manager.decide(first.id, True, "ui", "")
        carried: RunRecord = (await runtime.store.list_runs(1, chat.id))[0]
        assert carried.id != own.id
        async with asyncio.timeout(RUN_WAIT_S):
            await manager.wait(carried.id)
        assert await runtime.store.list_approvals("pending", 10) == []
        used: list[ApprovalRecord] = [
            record for record in await runtime.store.list_approvals(None, 20) if record.run_id == carried.id
        ]
        assert [(record.status, record.decided_by) for record in used] == [("approved", "permit")], used
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
