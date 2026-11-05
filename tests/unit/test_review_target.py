"""The reviewer of an ``auto`` run reads what an action acts on whole up to 12,000 characters (item 242), and hands a
longer one to the owner without asking the model (item 680): it read the start and the end of it, and what was cut out
between was approved unseen. Before item 242, it read 1,500 characters of it. It reads no secret: those Dot holds and
those it recognises are masked (item 350)."""

from pathlib import Path

import pytest

from dotagent.agent.review import REVIEW_TOOL, Reviewer, ReviewRequest, ReviewVerdict, reviewer_for
from dotagent.config import load_default_config
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.calls import ModelCall
from dotagent.llm.router import ModelRouter
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply
from dotagent.policy.engine import decide
from dotagent.policy.types import ActionInfo
from dotagent.secrets import resolve_secret
from dotagent.store.models import RuleRecord

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


async def _reviewed(command: str) -> tuple[ReviewVerdict, list[str]]:
    """How the review decides a shell call of ``command``, and what the review model was sent about it."""
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
    return verdict, provider.read


async def _read(command: str) -> str:
    """What the review model is sent about a shell call of ``command``, which it approves."""
    verdict, read = await _reviewed(command)
    assert verdict.decision == "approve"
    assert len(read) == 1
    return read[0]


async def test_the_reviewer_reads_a_long_command_whole_and_hands_a_longer_one_to_the_owner() -> None:
    command: str = _command(700)
    assert 9_000 < len(command) < 12_000
    assert command in await _read(command)

    longer: str = _command(2_000)
    verdict, read = await _reviewed(longer)
    assert verdict.decision == "escalate" and "the user decides it" in verdict.reason
    assert read == []


async def test_the_reviewer_reads_no_secret_of_the_action(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    monkeypatch.setenv("DOT_TEST_REVIEW_SECRET", "plain-value-9f3k2m7q")
    assert resolve_secret("DOT_TEST_REVIEW_SECRET") == "plain-value-9f3k2m7q"
    read: str = await _read(
        'curl -H "Authorization: Bearer sk-proj-abcdefghijklmnopqrstuvwx1234" -d plain-value-9f3k2m7q '
        "https://api.example.com/upload"
    )
    assert "sk-proj-abcdefghijklmnopqrstuvwx1234" not in read and "plain-value-9f3k2m7q" not in read
    assert "https://api.example.com/upload" in read


async def _review_script(script: str) -> tuple[ReviewVerdict, list[str]]:
    """How the review decides a page script of ``script`` run on a page, and what the review model was sent of it."""
    provider: ReadProvider = ReadProvider()
    router: ModelRouter = ModelRouter(load_default_config(), _ignore, {})
    await router.register("ollama", provider)
    review: Reviewer = reviewer_for(router, "ollama:scripted", 30.0, "review:test", None)
    action: ActionInfo = ActionInfo(
        tool="browser_eval",
        kind="browser_click",
        risk="medium",
        summary=f"Run a script in the page: {script[:159]}…",
        target="https://shop.example/",
        flags=(),
        body=script,
    )
    verdict: ReviewVerdict = await review(
        ReviewRequest(instructions=("Read the order.",), action=action, reason="the preset asks", tainted=True)
    )
    return verdict, provider.read


async def test_a_page_script_is_reviewed_and_ruled_on_whole_not_by_its_start() -> None:
    """Item 985, the security audit of 2026-11-05: browser_eval's action held the first 159 characters of the script
    and the page's address; the owner's rules and the review judged that while the whole script ran."""
    script: str = "(() => { /*" + "x" * 200 + "*/ document.forms[0].submit(); })()"
    verdict, read = await _review_script(script)
    assert verdict.decision == "approve" and "document.forms[0].submit()" in read[0]
    longer: str = "/*" + "x" * 13_000 + "*/ fetch('/pay', {method: 'POST'})"
    verdict, read = await _review_script(longer)
    assert verdict.decision == "escalate" and read == []
    held: ActionInfo = ActionInfo(
        tool="browser_eval", kind="browser_click", risk="medium", summary="Run a script in the page: (() => { /*xx…",
        target="https://shop.example/", flags=(), body=script,
    )  # fmt: skip
    rule: RuleRecord = RuleRecord(
        id="rule-1", position=0, enabled=True, behavior="handoff", tool_glob="browser_eval", match_regex=r"submit\(",
        description="", created_at=0.0,
    )  # fmt: skip
    assert decide(held, "normal", False, "aggressive", [rule], None).verdict == "handoff"
