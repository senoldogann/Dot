"""The reviewer of an ``auto`` run reads what an action acts on whole up to 12,000 characters, and of a longer target
its start and its end, with how much was left out between (item 242). Before, it read 1,500 characters of it."""

from dotagent.agent.review import REVIEW_TOOL, Reviewer, ReviewRequest, ReviewVerdict, reviewer_for
from dotagent.config import load_default_config
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.calls import ModelCall
from dotagent.llm.router import ModelRouter
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply
from dotagent.policy.types import ActionInfo

_APPROVE: ScriptedTurn = ScriptedTurn(
    text="",
    calls=(ScriptedCall(tool=REVIEW_TOOL, arguments={"decision": "approve", "reason": "It is what was asked."}),),
)


class ReadProvider(ScriptedProvider):
    """The scripted model, approving each action, which keeps the last message of each request it is sent."""

    def __init__(self) -> None:
        super().__init__("ollama", (_APPROVE,))
        self.read: list[str] = []

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.read.append(request.messages[-1].content)
        return await super().complete(request, sink)


async def _ignore(call: ModelCall) -> None:
    return None


def _command(names: int) -> str:
    """A shell command that makes ``names`` empty files, 16 characters each."""
    return "touch " + " ".join(f"shelf-{number:05}.txt" for number in range(names))


async def _read(command: str) -> str:
    """What the review model is sent about a shell call of ``command``."""
    provider: ReadProvider = ReadProvider()
    router: ModelRouter = ModelRouter(load_default_config(), _ignore, {})
    await router.register("ollama", provider)
    review: Reviewer = reviewer_for(router, "ollama:scripted", 30.0, "review:test", None)
    action: ActionInfo = ActionInfo(
        tool="shell", kind="shell", risk="low", summary="Run: touch", target=command, flags=()
    )
    verdict: ReviewVerdict = await review(
        ReviewRequest(
            instructions=("Make a file for each shelf.",),
            action=action,
            reason="the preset asks",
            tainted=False,
        )
    )
    assert verdict.decision == "approve"
    assert len(provider.read) == 1
    return provider.read[0]


async def test_the_reviewer_reads_a_long_command_whole_and_the_start_and_the_end_of_a_longer_one() -> None:
    command: str = _command(700)
    assert 9_000 < len(command) < 12_000
    assert command in await _read(command)

    longer: str = _command(2_000)
    read: str = await _read(longer)
    assert longer[:7_000] in read
    assert longer[-3_500:] in read
    assert "chars omitted" in read
    assert "shelf-01000.txt" not in read
