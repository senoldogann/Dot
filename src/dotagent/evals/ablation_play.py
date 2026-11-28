"""The blind judge of an ablation (docs/development.md, "dotx eval ablate"): each pair of answers sent to the judge
model in a throw-away home, in the order :func:`dotagent.evals.ablation.left_shown_first` draws, and the pick
unblinded. A judge that does not pick fails the ablation: a pair left out would move the margin unseen."""

from collections.abc import Mapping, Sequence

from dotagent.config import AppConfig
from dotagent.core.errors import ProviderProtocolError
from dotagent.core.text import truncate_middle
from dotagent.evals.ablation import (
    PICK_TOOL,
    Pairing,
    judge_messages,
    left_shown_first,
    pick_tool,
    shown_pick,
    unblinded,
)
from dotagent.evals.real_runs import RealCase
from dotagent.evals.results import TaskOutcome
from dotagent.evals.runner import eval_environment, scratch_home
from dotagent.llm.calls import CallOrigin
from dotagent.llm.router import ModelRef, parse_model_ref
from dotagent.llm.types import ChatRequest, ModelReply, StreamDelta, ToolCall
from dotagent.runtime import Runtime, close_runtime, open_runtime

_ORIGIN: CallOrigin = CallOrigin(run_id=None, purpose="harness_select")
_PROBLEM_CHARS: int = 300


async def _discard(delta: StreamDelta) -> None:
    return None


def _answer(outcome: TaskOutcome) -> str:
    """What an attempt answered, with how it ended when it did not end done."""
    return outcome.answer if outcome.status == "done" else f"(the run ended {outcome.status})\n{outcome.answer}"


async def _pick(runtime: Runtime, ref: ModelRef, goal: str, first: str, second: str) -> ModelReply:
    request: ChatRequest = ChatRequest(
        model=ref.model,
        messages=judge_messages(goal, first, second),
        tools=(pick_tool(),),
        temperature=None,
        max_output_tokens=None,
        effort="off",
        speed="standard",
        session_key="ablation-judge",
        cache_mark=None,
        prompt_reuse="single",
        window=await runtime.router.window_of(ref),
    )
    return await runtime.router.complete(ref, request, _discard, _ORIGIN)


async def judge_pairs(
    config: AppConfig,
    login_env: Mapping[str, str],
    secrets: Mapping[str, str],
    judge: str,
    cases: Sequence[RealCase],
    pairs: Sequence[tuple[str, str]],
    outcomes: Mapping[tuple[str, str], TaskOutcome],
) -> tuple[Pairing, ...]:
    """Each of ``pairs`` (left arm, right arm) of each case judged blind by ``judge``."""
    ref: ModelRef = parse_model_ref(judge)
    pairings: list[Pairing] = []
    with scratch_home() as home, eval_environment(home, secrets):
        runtime: Runtime = await open_runtime(config, dict(login_env))
        try:
            for case in cases:
                for left, right in pairs:
                    mine, theirs = outcomes[(case.id, left)], outcomes[(case.id, right)]
                    left_first: bool = left_shown_first(case.id, left, right)
                    first, second = (mine, theirs) if left_first else (theirs, mine)
                    reply: ModelReply = await _pick(runtime, ref, case.goal, _answer(first), _answer(second))
                    calls: list[ToolCall] = [call for call in reply.tool_calls if call.name == PICK_TOOL]
                    if not calls:
                        raise ProviderProtocolError(
                            f"the judge did not call {PICK_TOOL}",
                            {"model": ref.label, "case": case.id, "text": truncate_middle(reply.text, _PROBLEM_CHARS)},
                        )
                    pairings.append(
                        Pairing(
                            case_id=case.id,
                            left=left,
                            right=right,
                            pick=unblinded(shown_pick(calls[0].arguments), left_first),
                            left_tokens=mine.metrics.input_tokens + mine.metrics.output_tokens,
                            right_tokens=theirs.metrics.input_tokens + theirs.metrics.output_tokens,
                        )
                    )
        finally:
            await close_runtime(runtime)
    return tuple(pairings)
