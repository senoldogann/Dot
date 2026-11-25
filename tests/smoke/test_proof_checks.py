"""Proof checks in the real loop, gate and store (item 1853): a task run whose owner set a check is sent back while it
fails, with what it printed, and is done once it passes, asking nothing in yolo; a check the gate refuses ends the run
failed instead of leaving the decision to a model; a campaign's claim is refused while the campaign's check fails; a
chat is refused checks.

No network and no model service is used: the scripted model plays the runs.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.agent.proof import PROOF_CHECKED_EVENT, PROOF_EVENT
from dotagent.config import AppConfig, load_default_config
from dotagent.core.errors import ConfigError
from dotagent.core.types import JsonObject
from dotagent.evals import EVAL_SOURCE
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import Message
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.models import NO_PICKS, CampaignRecord, EventRecord, ProofCheck, RunRecord
from dotagent.tools.campaign import CAMPAIGN_ACHIEVED_TOOL, CAMPAIGN_TARGETS_TOOL
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Write done.txt in the workspace."
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 60.0
_MAX_EVENTS: int = 10_000
_DONE: ProofCheck = ProofCheck(command="test -f done.txt", timeout_s=30, by="owner")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _config(approval_timeout_s: float) -> AppConfig:
    config: AppConfig = eval_config(load_default_config())
    return config.model_copy(
        update={
            "models": config.models.model_copy(update={"review": SCRIPTED_MODEL}),
            "policy": config.policy.model_copy(update={"approval_timeout_s": approval_timeout_s}),
        }
    )


@asynccontextmanager
async def _session(
    config: AppConfig, login_env: dict[str, str], turns: list[ScriptedTurn]
) -> AsyncIterator[tuple[Runtime, RunManager]]:
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, turns))
            manager: RunManager = RunManager(runtime)
            try:
                yield runtime, manager
            finally:
                await manager.shutdown()
        finally:
            await close_runtime(runtime)


def _turn(*calls: ScriptedCall) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=calls)


def _call(tool: str, arguments: JsonObject) -> ScriptedCall:
    return ScriptedCall(tool=tool, arguments=arguments)


def _finish(answer: str) -> ScriptedTurn:
    return _turn(_call(FINISH_TOOL, {"answer": answer, "success": True}))


async def _events(runtime: Runtime, run_id: str, kind: str) -> list[JsonObject]:
    return [event.data for event in await runtime.store.list_events(run_id, 0, _MAX_EVENTS) if event.kind == kind]


def _ends(looks: list[JsonObject]) -> list[list[str]]:
    """How each check ended, look by look, from the ``proof_check`` events."""
    ends: list[list[str]] = []
    for look in looks:
        checks: object = look["checks"]
        assert isinstance(checks, list)
        ends.append([str(check["end"]) for check in checks if isinstance(check, dict)])
    return ends


async def _start(manager: RunManager, approvals: str, proof: tuple[ProofCheck, ...]) -> RunRecord:
    return await manager.start_picked(
        GOAL,
        "task",
        SCRIPTED_MODEL,
        None,
        EVAL_SOURCE,
        None,
        None,
        "standard",
        None,
        "yolo" if approvals == "yolo" else "ask",
        False,
        (),
        NO_PICKS,
        proof=proof,
    )


async def test_a_failing_check_sends_the_run_back_with_its_output_and_a_passing_one_lets_it_end_done_asking_nothing(
    login_env: dict[str, str],
) -> None:
    turns: list[ScriptedTurn] = [
        _finish("done.txt is written."),
        _turn(_call("shell", {"command": "touch done.txt"})),
        _finish("done.txt is written now."),
    ]
    async with _session(_config(30.0), login_env, turns) as (runtime, manager):
        run: RunRecord = await _start(manager, "yolo", (_DONE,))
        async with asyncio.timeout(_TIME_LIMIT_S):
            ended: RunRecord = await manager.wait(run.id)
        set_checks: list[JsonObject] = await _events(runtime, run.id, PROOF_EVENT)
        looks: list[JsonObject] = await _events(runtime, run.id, PROOF_CHECKED_EVENT)
        asked: list[JsonObject] = await _events(runtime, run.id, "approval_requested")
        messages: list[Message] = await runtime.store.load_messages(run.thread_id, 0)
    assert (ended.status, ended.result) == ("done", "done.txt is written now."), ended.result
    assert set_checks == [{"command": "test -f done.txt", "timeout_s": 30, "by": "owner"}]
    assert _ends(looks) == [["failed"], ["passed"]]
    # A yolo run asks nothing for its checks.
    assert asked == []
    notes: list[str] = [message.content for message in messages if message.role == "user"]
    assert any(
        note.startswith("[system note] The proof does not pass yet") and "test -f done.txt" in note for note in notes
    )


async def test_a_check_the_gate_refuses_ends_the_run_failed_and_leaves_nothing_to_a_model(
    login_env: dict[str, str],
) -> None:
    # Nobody answers the question the privileged line asks in ask mode, so it counts as a refusal at once.
    refused: ProofCheck = ProofCheck(command="sudo -n true", timeout_s=30, by="owner")
    async with _session(_config(0.5), login_env, [_finish("Done.")]) as (runtime, manager):
        run: RunRecord = await _start(manager, "ask", (refused,))
        async with asyncio.timeout(_TIME_LIMIT_S):
            ended: RunRecord = await manager.wait(run.id)
        looks: list[JsonObject] = await _events(runtime, run.id, PROOF_CHECKED_EVENT)
    assert ended.status == "failed", ended.result
    assert "the proof could not run, so the answer is not proven: `sudo -n true`" in ended.result
    assert _ends(looks) == [["not_run"]]


async def test_a_campaigns_claim_is_refused_while_its_check_fails(login_env: dict[str, str]) -> None:
    claim: ScriptedCall = _call(CAMPAIGN_ACHIEVED_TOOL, {"evidence": "done.txt is there."})
    targets: ScriptedCall = _call(CAMPAIGN_TARGETS_TOOL, {"targets": "done.txt exists", "verification": "test -f"})
    turns: list[ScriptedTurn] = [_turn(targets), _turn(claim), _finish("Not done yet.")]
    async with _session(_config(30.0), login_env, turns) as (runtime, manager):
        opening: RunRecord = await manager.start_campaign(
            GOAL, "task", SCRIPTED_MODEL, None, EVAL_SOURCE, None, "standard", None, "yolo", None, (), None, (_DONE,)
        )
        assert opening.campaign_id is not None
        async with asyncio.timeout(_TIME_LIMIT_S):
            await manager.wait(opening.id)
        campaign: CampaignRecord = await runtime.store.get_campaign(opening.campaign_id)
        events: list[EventRecord] = await runtime.store.list_events(opening.id, 0, _MAX_EVENTS)
    results: list[JsonObject] = [
        event.data
        for event in events
        if event.kind == "tool_result" and event.data.get("tool") == CAMPAIGN_ACHIEVED_TOOL
    ]
    nested: list[JsonObject] = [
        event.data for event in events if event.kind == "tool_call" and event.data.get("via") == "proof"
    ]
    assert campaign.status == "active"
    assert campaign.proofs == (_DONE,)
    assert len(results) == 1 and "the campaign's proof does not pass" in str(results[0]["text"])
    assert [call["tool"] for call in nested] == ["shell"]


async def test_a_chat_is_refused_checks(login_env: dict[str, str]) -> None:
    async with _session(_config(30.0), login_env, []) as (_, manager):
        with pytest.raises(ConfigError, match="proof checks hold a task's finish"):
            await manager.start_picked(
                GOAL,
                "chat",
                SCRIPTED_MODEL,
                None,
                EVAL_SOURCE,
                None,
                None,
                "standard",
                None,
                "yolo",
                None,
                (),
                NO_PICKS,
                proof=(_DONE,),
            )
