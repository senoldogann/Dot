"""The review model reads the owner's instructions whole (item 1947; K3-05 of the code review of 2026-11-19): it read
the twelve newest, each cut to 1,500 characters from the middle, so a "never mail the client, only draft" in the middle
of a pasted brief was lost and an ``auto`` run's send was approved. Each instruction is read whole now, the newest
whatever its length, then the first and the others newest first as they fit; the newest twelve of the rest are shown
cut, as before, and any others are named."""

from dotagent.agent.review import (
    REVIEW_TOOL,
    Reviewer,
    ReviewRequest,
    ReviewVerdict,
    render_instructions,
    review_reads_instructions,
    reviewer_for,
)
from dotagent.config import load_default_config
from dotagent.core.text import truncate_middle
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.calls import ModelCall
from dotagent.llm.router import ModelRouter
from dotagent.llm.types import ChatRequest, DeltaSink, ModelReply
from dotagent.policy.types import ActionInfo

_CONSTRAINT: str = "Never mail the client, only draft the mail and leave it for me."
# A brief of about 3,000 characters with the constraint in its middle.
_BRIEF: str = (
    "Here is the project brief.\n"
    + "The client wants a quote for the redesign of their shop, with the pages listed below. " * 17
    + f"\n{_CONSTRAINT}\n"
    + "The pages are the home page, the catalogue, a product and the checkout, each in two languages. " * 17
)
_DENY: ScriptedTurn = ScriptedTurn(
    text="",
    calls=(ScriptedCall(tool=REVIEW_TOOL, arguments={"decision": "deny", "reason": "The owner said only draft."}),),
)


class _ReadProvider(ScriptedProvider):
    """The scripted model, which keeps the last message of each request it is sent."""

    def __init__(self) -> None:
        super().__init__("ollama", (_DENY,))
        self.read: list[str] = []

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.read.append(request.messages[-1].content)
        return await super().complete(request, sink)


async def _ignore(call: ModelCall) -> None:
    return None


async def test_a_constraint_in_the_middle_of_a_long_brief_reaches_the_review_model_whole() -> None:
    provider: _ReadProvider = _ReadProvider()
    router: ModelRouter = ModelRouter(load_default_config(), _ignore, {})
    await router.register("ollama", provider)
    review: Reviewer = reviewer_for(router, "ollama:scripted", 30.0, "review:test", None)
    send: ActionInfo = ActionInfo(
        tool="shell",
        kind="shell",
        risk="medium",
        summary="Send the quote to the client",
        target="mail -s Quote client@example.invalid < quote.txt",
        flags=("external_send", "message_send"),
    )
    assert len(_BRIEF) > 3000
    verdict: ReviewVerdict = await review(
        ReviewRequest(instructions=(_BRIEF, "Go on with the quote."), action=send, reason="a mail", tainted=False)
    )
    assert verdict.decision == "deny"
    (read,) = provider.read
    assert f"1. {_BRIEF}\n2. Go on with the quote." in read


def test_the_newest_and_the_first_are_read_whole_and_the_others_as_they_fit_the_rest_cut_as_before() -> None:
    long: list[str] = [f"instruction {number}: " + "x" * 9_000 for number in range(1, 11)]
    newest: str = "the newest: " + "y" * 20_000
    rendered: str = render_instructions((*long, newest))
    assert rendered.startswith("(Instructions too long to read whole with the others are cut from their middle")
    for number in (1, 8, 9, 10):
        assert f"\n{number}. {long[number - 1]}\n" in rendered
    for number in (2, 3, 4, 5, 6, 7):
        assert f"\n{number}. [cut] {truncate_middle(long[number - 1], 1500)}\n" in rendered
    assert rendered.endswith(f"\n11. {newest}")
    assert not review_reads_instructions((*long, newest))
    assert review_reads_instructions((_BRIEF, *long[:5]))
    assert render_instructions(("only one",)) == "1. only one"
    assert render_instructions(()) == ""


def test_a_short_restriction_behind_a_long_newest_message_is_still_read() -> None:
    """The security review of item 1947: the newest message, read whole, left no room for the others, so yolo's check
    of a tainted run, which asks nobody, judged a mail without "Never email anyone". The review reads at least what it
    read before: the newest twelve instructions it cannot read whole are shown cut, as all were then."""
    newest: str = "z" * 50_000
    rendered: str = render_instructions(("Plan my trip. " + "p" * 10_000, "Never email anyone.", newest))
    assert "\n2. Never email anyone.\n" in rendered
    assert rendered.endswith(f"\n3. {newest}")
    many: tuple[str, ...] = (*(f"step {number} " + "s" * 9_000 for number in range(1, 21)), "n" * 55_000)
    shown: str = render_instructions(many)
    assert "(Instructions 1, 2, 3, 4, 5, 6, 7, 8 are left out: too long to read with the others.)" in shown
    assert all(f"\n{number}. [cut] step {number} " in shown for number in range(9, 21))
