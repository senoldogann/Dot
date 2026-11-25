"""The hop counter across agents (item 1741): every hand-off from one agent to another (a delegated or messaged run, a
work order's attempt, the lead's run woken by an answer, a thing a run put on Dot's list) is one hop further than the
run it came from; the manager sets it, and nothing a run says lowers it. Past ``[agent] max_hops`` the hand-off is not
made: it is logged and the run it came from gets a card for the owner (Allow once, Stop). In yolo and in free mode
nothing is refused (docs/freedom.md): the hand-off is made, logged, and the owner is told once, no question asked. A
real RunManager on the scripted model; no network."""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.loop import RunOpening
from dotagent.agent.manager import HOP_CARD_ALLOW, HOP_CARD_KEY, RunManager
from dotagent.config import load_config
from dotagent.core.errors import HopLimitReached
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.notify.tellkinds import TellKind
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import NO_PICKS, ApprovalRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = pytest.mark.asyncio(loop_scope="module")

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_LIMIT: int = 1
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Done.", "success": True}),)
)
_DELEGATE: ScriptedTurn = ScriptedTurn(
    text="",
    calls=(ScriptedCall(tool="delegate", arguments={"goal": "Count the files in the workspace.", "wait": True}),),
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def manager(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[RunManager]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        "[browser]\nheadless = true\n\n[agent]\nplaybooks = false\ngoal_mode = false\n"
        f'max_hops = {_LIMIT}\n\n[models]\nmain = "{SCRIPTED_MODEL}"\nreview = "{SCRIPTED_MODEL}"\n\n'
        "[memory]\nembed_enabled = false\n",
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    # The lead delegates and ends; the helper ends; the run its answer wakes delegates again, which the limit refuses,
    # and ends.
    await runtime.router.register(
        _PROVIDER,
        ScriptedProvider(_PROVIDER, [_DELEGATE, _FINISH, _FINISH, _DELEGATE, _FINISH, _FINISH, *([_FINISH] * 24)]),
    )
    built: RunManager = RunManager(runtime)
    try:
        yield built
    finally:
        await built.shutdown()
        await close_runtime(runtime)
        patch.undo()


async def _children(manager: RunManager, parent: RunRecord) -> list[RunRecord]:
    runs = await manager._runtime.store._all("SELECT id FROM runs WHERE parent_run_id = ?", (parent.id,))
    return [await manager._runtime.store.get_run(str(row["id"])) for row in runs]


async def _cards(manager: RunManager, run: RunRecord) -> list[ApprovalRecord]:
    return [
        card
        for card in await manager._runtime.store.list_left_questions(run.id)
        if card.args.get(HOP_CARD_KEY) is not None
    ]


async def test_a_hand_off_is_one_hop_further_a_hand_back_keeps_the_count_and_yolo_refuses_nothing_past_the_limit(
    manager: RunManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = manager._runtime.store
    told: list[tuple[str, str]] = []

    async def noted(bus: object, title: str, text: str, url: str, kind: TellKind, subject: str) -> None:
        assert kind == "system"
        told.append((title, url))

    monkeypatch.setattr("dotagent.agent.manager.tell_owner", noted)
    lead: RunRecord = await manager.start(
        "Run the team.", "task", SCRIPTED_MODEL, None, "ui", None, None, "standard", None, "yolo", False, ()
    )
    async with asyncio.timeout(60):
        assert (await manager.wait(lead.id)).status == "done"
    assert (await store.get_run(lead.id)).hops == 0
    helpers: list[RunRecord] = await _children(manager, lead)
    assert [helper.hops for helper in helpers] == [1], "a delegated run is one hand-off from the run that delegated it"
    assert told == []
    # The helper's answer wakes the lead's chat: the run is as far from the start as the helper it reads.
    woken: RunRecord | None = await manager.wake_lead(
        lead.thread_id, "The agent's answer came back.", "Done.", False, (helpers[0].id,)
    )
    assert woken is not None
    assert woken.hops == 1, "a hand-back does not reset the count"
    async with asyncio.timeout(60):
        await manager.wait(woken.id)
    # The woken run delegated once more: hop 2, over the limit of 1. In yolo it is made all the same, no card is left
    # and nothing waits; the owner is told, once.
    made: list[RunRecord] = await _children(manager, woken)
    assert [child.hops for child in made] == [2]
    assert await _cards(manager, woken) == []
    assert len(told) == 1 and told[0][1] == f"#/runs/{woken.id}"
    ended: RunRecord = await store.get_run(woken.id)
    assert ended.approvals == "yolo" and ended.status == "done"


async def test_a_run_that_asks_is_refused_past_the_limit_and_yolo_and_free_mode_are_not(manager: RunManager) -> None:
    asking: RunRecord = await manager.start(
        "Anything.", "task", SCRIPTED_MODEL, None, "ui", None, None, "standard", None, "ask", False, ()
    )
    yolo: RunRecord = await manager.start(
        "Anything too.", "task", SCRIPTED_MODEL, None, "ui", None, None, "standard", None, "yolo", False, ()
    )
    for run in (asking, yolo):
        async with asyncio.timeout(60):
            await manager.wait(run.id)

    async def launch(origin: RunRecord, approvals: str) -> RunRecord:
        opening: RunOpening = RunOpening(
            text="x",
            files=(),
            resumed=None,
            once=None,
            carried=(),
            picks=NO_PICKS,
            hops=_LIMIT + 1,
            from_run_id=origin.id,
        )
        run, _ = await manager._launch(
            "Hand it on", "task", None, None, "delegate", None, None, "standard", approvals, False, opening, None, None
        )
        return run

    with pytest.raises(HopLimitReached):
        await launch(asking, "ask")
    assert len(await _cards(manager, asking)) == 1
    # A yolo run that hands work to an agent set to ask is refused nothing either: the run it comes from counts.
    assert (await launch(yolo, "ask")).hops == _LIMIT + 1
    assert await _cards(manager, yolo) == []
    assert (await launch(asking, "yolo")).hops == _LIMIT + 1
    manager._runtime.approvals.switch_free(True)
    try:
        assert (await launch(asking, "ask")).hops == _LIMIT + 1, "free mode refuses nothing, whatever the run's mode"
    finally:
        manager._runtime.approvals.switch_free(False)
    with pytest.raises(HopLimitReached):
        await launch(asking, "ask")


async def test_the_stop_leaves_one_card_while_one_stands_and_the_limit_itself_allows_its_own_hop(
    manager: RunManager,
) -> None:
    base: RunRecord = await manager.start(
        "Anything.", "task", SCRIPTED_MODEL, None, "ui", None, None, "standard", None, "yolo", False, ()
    )
    async with asyncio.timeout(60):
        await manager.wait(base.id)
    over: RunOpening = RunOpening(
        text="x", files=(), resumed=None, once=None, carried=(), picks=NO_PICKS, hops=_LIMIT + 1, from_run_id=base.id
    )
    for _ in range(3):
        with pytest.raises(HopLimitReached) as stopped:
            await manager._keep_to_the_hop_limit(over, "Hand it on")
        assert "not made" in stopped.value.message
    assert len(await _cards(manager, base)) == 1, "a model that tries again does not fill the owner's page with cards"
    at_the_limit: RunOpening = over.model_copy(update={"hops": _LIMIT})
    await manager._keep_to_the_hop_limit(at_the_limit, "the last one allowed")


async def test_a_thing_one_run_put_on_dots_list_is_a_hop_further_for_the_run_that_does_it(manager: RunManager) -> None:
    store = manager._runtime.store
    origin: RunRecord = await store.create_run(
        (await store.create_thread("Origin", "ui")).id,
        "goal",
        "task",
        SCRIPTED_MODEL,
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
        hops=_LIMIT,
    )
    chat = await store.create_thread("Initiative", "initiative")
    # Dot's own initiative runs in yolo: past the limit the run is made, one hand-off further, and nothing is asked.
    run: RunRecord = await manager.start_own("Do the thing", "Do it.", "task", chat.id, False, None, origin.id)
    assert run.hops == _LIMIT + 1
    assert await _cards(manager, origin) == []


async def test_no_tool_takes_a_count_of_hops_so_no_run_can_set_or_lower_it(manager: RunManager) -> None:
    for name, tool in manager._runtime.registry.tools.items():
        assert "hops" not in json.dumps(tool.spec.parameters), f"{name} lets a run name the hop count"


async def test_allow_once_begins_a_new_count_and_stop_does_not(manager: RunManager) -> None:
    store = manager._runtime.store
    base: RunRecord = await manager.start(
        "Anything else.", "task", SCRIPTED_MODEL, None, "ui", None, None, "standard", None, "yolo", False, ()
    )
    async with asyncio.timeout(60):
        await manager.wait(base.id)
    await store.set_run_hops(base.id, _LIMIT)
    over: RunOpening = RunOpening(
        text="x", files=(), resumed=None, once=None, carried=(), picks=NO_PICKS, hops=_LIMIT + 1, from_run_id=base.id
    )
    with pytest.raises(HopLimitReached):
        await manager._keep_to_the_hop_limit(over, "Hand it on")
    [card] = await _cards(manager, base)
    await manager.decide(card.id, True, "ui", "stop it, please")
    assert (await store.get_run(base.id)).hops == _LIMIT, "anything but Allow once leaves the count"
    with pytest.raises(HopLimitReached):
        await manager._keep_to_the_hop_limit(over, "Hand it on again")
    [again] = await _cards(manager, base)
    await manager.decide(again.id, True, "ui", HOP_CARD_ALLOW)
    assert (await store.get_run(base.id)).hops == 0, "Allow once: the run's next hand-off counts from the owner's word"
