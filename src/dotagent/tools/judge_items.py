"""The judge_items tool: one question about every item of a list, answered by TypeSafe's System One model in one request
and handed back ranked, so the main model reads a short ordered list instead of weighing each item in its own words.

Without levels every item gets the probability of yes to the question (a Noul of TypeSafe's); with two to ten ordered
levels every item gets its expected level and the likeliest one (a Score). The ids stay in the code: the state of a
request holds the items by id (``{"candidates": {id: text}}``), and each item's question names its path, so the
question carries the whole meaning. A list longer than ``[judge] items_per_request`` goes in several requests.

The tool shares the :class:`dotagent.agent.judge.TypeSafeService` of the runtime with the typed judge, so an outage
pauses both, and it is offered when the key named by ``[judge] api_key_env`` is saved, whatever ``[judge] provider``
says. The ranking is a probability, not a fact: the decision stays with the main model.
"""

from collections.abc import Sequence
from urllib.parse import urlsplit

from pydantic import Field, field_validator

from dotagent.agent.judge import TypeSafeService
from dotagent.core.errors import ProviderError
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.typesafe import MAX_LEVELS, MIN_LEVELS, NoulQuestion, ScoreAnswer, ScoreQuestion
from dotagent.policy.types import ActionInfo
from dotagent.tools.base import ALL_MODES, RegisteredTool, ToolArgs, ToolResult, fail, make_tool, ok
from dotagent.tools.context import ToolContext

JUDGE_ITEMS_TOOL: str = "judge_items"
# Several requests of [judge] timeout_s each fit in here.
_TIMEOUT_S: float = 300.0
_ID_PATTERN: str = r"^[A-Za-z0-9_-]{1,64}$"
_MAX_ITEMS: int = 500
_TEXT_CHARS: int = 4000
_QUESTION_CHARS: int = 1000
_LEVEL_CHARS: int = 300
_SHOWN_CHARS: int = 60
_SUMMARY_CHARS: int = 120


class Item(FrozenModel):
    """One candidate: an id the ranking gives back and the text to judge."""

    id: str = Field(
        pattern=_ID_PATTERN,
        description="Letters, digits, - and _ only; the ranking names the item by it.",
    )
    text: str = Field(
        min_length=1,
        max_length=_TEXT_CHARS,
        description="What there is to judge: a title and a snippet, a post, a link with its context, a file's head.",
    )


class JudgeItemsArgs(ToolArgs):
    question: str = Field(
        min_length=1,
        max_length=_QUESTION_CHARS,
        description=(
            'One question about an item, complete in itself. Without levels a yes/no question ("Is this post about '
            'Python packaging, written by a person?"); with levels a question of degree ("How relevant is this hit '
            'to the search for …?").'
        ),
    )
    items: list[Item] = Field(min_length=1, max_length=_MAX_ITEMS, description="The items to judge, each with an id.")
    levels: list[str] = Field(
        default_factory=list,
        max_length=MAX_LEVELS,
        description=(
            "Leave empty for a yes/no question. Else 2 to 10 ordered levels, the lowest first, each describing a "
            'concrete situation on its own ("unrelated", "mentions the topic in passing", "about the topic").'
        ),
    )

    @field_validator("levels")
    @classmethod
    def _enough_levels(cls, levels: list[str]) -> list[str]:
        if levels and len(levels) < MIN_LEVELS:
            raise ValueError(f"levels needs at least {MIN_LEVELS} entries, or none for a yes/no question")
        if any(not level.strip() or len(level) > _LEVEL_CHARS for level in levels):
            raise ValueError(f"every level is a description of 1 to {_LEVEL_CHARS} characters")
        return levels

    @field_validator("items")
    @classmethod
    def _distinct_ids(cls, items: list[Item]) -> list[Item]:
        ids: list[str] = [item.id for item in items]
        repeated: list[str] = sorted({item_id for item_id in ids if ids.count(item_id) > 1})
        if repeated:
            raise ValueError(f"item ids repeat: {', '.join(repeated)}")
        return items


class Judged(FrozenModel):
    """TypeSafe's answer about one item: the probability of yes, or the expected level with the likeliest level."""

    id: str
    value: float
    level: str | None


def batches(items: Sequence[Item], size: int) -> tuple[tuple[Item, ...], ...]:
    """``items`` in groups of at most ``size``, in their order."""
    return tuple(tuple(items[start : start + size]) for start in range(0, len(items), size))


def state_of(batch: Sequence[Item]) -> JsonObject:
    """The state of one request: the items by id, where each question finds its own."""
    return {"candidates": {item.id: item.text for item in batch}}


def _instructions(question: str, item: Item) -> str:
    return f"About the candidate at `candidates.{item.id}`: {question}"


def noul_questions(question: str, batch: Sequence[Item]) -> tuple[NoulQuestion, ...]:
    """One yes/no question per item of ``batch``, by the item's id."""
    return tuple(NoulQuestion(id=item.id, instructions=_instructions(question, item)) for item in batch)


def score_questions(question: str, batch: Sequence[Item], levels: Sequence[str]) -> tuple[ScoreQuestion, ...]:
    """One rating on ``levels`` per item of ``batch``, by the item's id."""
    return tuple(
        ScoreQuestion(id=item.id, instructions=_instructions(question, item), levels=tuple(levels)) for item in batch
    )


def judged_scores(answers: dict[str, ScoreAnswer], levels: Sequence[str]) -> tuple[Judged, ...]:
    """The score answers as judged items: the expected level and the likeliest level's description."""
    return tuple(
        Judged(
            id=item_id,
            value=answer.score,
            level=levels[max(range(len(answer.probabilities)), key=answer.probabilities.__getitem__)],
        )
        for item_id, answer in answers.items()
    )


def ranked(judged: Sequence[Judged]) -> tuple[Judged, ...]:
    """``judged`` from the highest value down; equal values keep their order."""
    return tuple(sorted(judged, key=lambda item: -item.value))


def _shown(text: str) -> str:
    flat: str = " ".join(text.split())
    return flat if len(flat) <= _SHOWN_CHARS else f"{flat[: _SHOWN_CHARS - 1]}…"


def render(question: str, judged: Sequence[Judged], texts: dict[str, str], levels: Sequence[str], requests: int) -> str:
    """The ranking as the model reads it: one line per item, the highest first, with the start of its text."""
    plural: str = "" if requests == 1 else "s"
    if levels:
        scale: str = ", ".join(f"{number} = {level}" for number, level in enumerate(levels))
        head: str = (
            f"{len(judged)} items rated on “{question}” in {requests} request{plural}; the expected level (where "
            f"{scale}) and the likeliest level, the highest first:"
        )
        lines: list[str] = [
            f"{rank}. {item.id} {item.value:.2f} ({item.level}) — {_shown(texts[item.id])}"
            for rank, item in enumerate(judged, 1)
        ]
    else:
        head = (
            f"{len(judged)} items judged on “{question}” in {requests} request{plural}; the probability of yes, "
            "the likeliest yes first:"
        )
        lines = [
            f"{rank}. {item.id} {item.value:.2f} — {_shown(texts[item.id])}" for rank, item in enumerate(judged, 1)
        ]
    return "\n".join([head, *lines])


async def _judge(service: TypeSafeService, args: JudgeItemsArgs, run_id: str) -> tuple[tuple[Judged, ...], int] | None:
    """Every item judged, and the number of requests it took; ``None`` while the service is paused."""
    judged: list[Judged] = []
    groups: tuple[tuple[Item, ...], ...] = batches(args.items, service.config.items_per_request)
    for batch in groups:
        if args.levels:
            scores: dict[str, ScoreAnswer] | None = await service.scores(
                state_of(batch), score_questions(args.question, batch, args.levels), run_id
            )
            if scores is None:
                return None
            judged.extend(judged_scores(scores, args.levels))
        else:
            nouls: dict[str, float] | None = await service.nouls(
                state_of(batch), noul_questions(args.question, batch), run_id
            )
            if nouls is None:
                return None
            judged.extend(Judged(id=item_id, value=value, level=None) for item_id, value in nouls.items())
    return ranked(judged), len(groups)


def build_judge_items_tool() -> RegisteredTool:
    def classify(args: JudgeItemsArgs, ctx: ToolContext) -> ActionInfo:
        flat: str = " ".join(args.question.split())
        shown: str = flat if len(flat) <= _SUMMARY_CHARS else f"{flat[: _SUMMARY_CHARS - 1]}…"
        return ActionInfo(
            tool=JUDGE_ITEMS_TOOL,
            kind="network",
            risk="low",
            summary=f"Judge {len(args.items)} items with TypeSafe: {shown}",
            target=urlsplit(ctx.runtime.config.judge.base_url).netloc,
            flags=(),
        )

    async def run(args: JudgeItemsArgs, ctx: ToolContext) -> ToolResult:
        service: TypeSafeService | None = ctx.runtime.typesafe
        if service is None:
            return fail(
                f"judge_items has no TypeSafe service: save the key {ctx.runtime.config.judge.api_key_env} and "
                "restart Dot. Judge the items yourself."
            )
        try:
            found: tuple[tuple[Judged, ...], int] | None = await _judge(service, args, ctx.run_id)
        except ProviderError as error:
            return fail(
                "TypeSafe rejected the request or answered outside its reference, so the items were not judged "
                f"(a judge_error note in the timeline has the details): {error.message}. Judge the items yourself, "
                "or ask again with a plainer question or plainer levels."
            )
        if found is None:
            return fail(
                "TypeSafe is unavailable for a while (a judge_unavailable note in the timeline says why). Judge the "
                "items yourself."
            )
        judged, requests = found
        return ok(render(args.question, judged, {item.id: item.text for item in args.items}, args.levels, requests))

    return make_tool(
        name=JUDGE_ITEMS_TOOL,
        description=(
            "Have a small typed model answer one question about every item of a list in one call, and get the items "
            "back ranked: without levels each gets the probability of yes to a yes/no question; with 2 to 10 ordered "
            "levels each gets its expected level. Use it to filter or order search hits, posts, links, files or "
            "playbooks before reading them, instead of weighing each in your own words; the decision stays yours."
        ),
        args_model=JudgeItemsArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=_TIMEOUT_S,
        classify=classify,
        run=run,
    )
