"""The post tools of a campaign on X or another feed, on TypeSafe's typed judgments: which posts are worth answering,
which of the drafts is the best reply, and whether a draft may be posted.

``rank_posts`` asks three yes/no questions about every post in one request (on the account's topic; written by a person,
not a bot, a brand or spam; worth a reply that adds something) and combines them with weights the campaign's ledger
holds, TypeSafe's composite-scoring pattern: the first call writes the weights as a ledger note with equal values, and
``campaign_note`` changes them (the numbers ``w_topic``, ``w_person`` and ``w_reply``) without asking TypeSafe again.
The scores of every ranking are kept as a ``posts_ranked`` event of the session. ``pick_draft`` asks one Choice over the
drafts; ``check_draft`` asks whether a draft is off topic, repeats one of the account's earlier posts or breaks a rule
the campaign's goal names, and refuses the draft when one of those reaches ``[judge] draft_check_threshold``.

They exist only in the sessions of a campaign and when TypeSafe's key is saved, share the service of the typed judge
and the judge_items tool, and are ``network`` actions of low risk: nothing here posts, the main model keeps every
decision.
"""

from collections.abc import Sequence
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import Field, JsonValue, StringConstraints, field_validator

from dotagent.agent.judge import TypeSafeService
from dotagent.core.errors import ProviderError
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.typesafe import ChoiceAnswer, ChoiceQuestion, NoulQuestion
from dotagent.policy.types import ActionInfo
from dotagent.store.models import CampaignEntry, CampaignRecord
from dotagent.tools.base import ALL_MODES, RegisteredTool, ToolArgs, ToolResult, fail, make_tool, ok
from dotagent.tools.campaign import campaign_of
from dotagent.tools.context import ToolContext

RANK_POSTS_TOOL: str = "rank_posts"
PICK_DRAFT_TOOL: str = "pick_draft"
CHECK_DRAFT_TOOL: str = "check_draft"
POST_TOOLS: frozenset[str] = frozenset({RANK_POSTS_TOOL, PICK_DRAFT_TOOL, CHECK_DRAFT_TOOL})
POSTS_RANKED_EVENT: str = "posts_ranked"
# The three questions of the ranking, by the name of their weight in the ledger.
WEIGHT_NAMES: tuple[str, str, str] = ("w_topic", "w_person", "w_reply")
_TIMEOUT_S: float = 300.0
_ID_PATTERN: str = r"^[A-Za-z0-9_-]{1,64}$"
_MAX_POSTS: int = 200
_MAX_DRAFTS: int = 10
_MIN_DRAFTS: int = 2
_MAX_EARLIER: int = 50
_MAX_RULES: int = 20
_TEXT_CHARS: int = 4000
_SHOWN_CHARS: int = 60
_SUMMARY_CHARS: int = 120
# The weights of the first ranking of a campaign, written in its ledger for the owner to change.
_EQUAL_WEIGHT: float = 1.0
_WEIGHTS_TEXT: str = (
    "Weights of rank_posts: on the topic (w_topic), by a person (w_person), worth a reply (w_reply). A campaign_note "
    "entry with these numbers changes them."
)
# The three questions, each with the weight it takes. {path} is the post's path in the state.
_QUESTIONS: tuple[tuple[str, str], ...] = (
    (
        "w_topic",
        "Is the post at `{path}` about the topic in `topic`, or close enough that an account on that topic would "
        "answer it?",
    ),
    (
        "w_person",
        "Is the post at `{path}` written by a person about their own experience, work or view, rather than by a bot, "
        "a brand's broadcast, spam, a giveaway or engagement bait?",
    ),
    (
        "w_reply",
        "Is the post at `{path}` worth a reply that adds something, such as an answer, an example or a question, "
        "because it asks, claims or describes a problem, rather than a closed statement, a joke or an announcement?",
    ),
)
_OFF_TOPIC: str = "off_topic"
_REPEATS: str = "repeats"
_RULE_PREFIX: str = "rule_"

type Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=_TEXT_CHARS)]


class Post(FrozenModel):
    """A post as the agent read it: an id the ranking gives back, who wrote it and its text."""

    id: str = Field(pattern=_ID_PATTERN, description="Letters, digits, - and _ only; the ranking names the post by it.")
    author: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] = Field(
        description=(
            "The account that wrote it, with what the page shows about it (name, handle, a bio line); may be ''."
        )
    )
    text: Text = Field(description="The post's text.")


class Draft(FrozenModel):
    id: str = Field(pattern=_ID_PATTERN, description="Letters, digits, - and _ only.")
    text: Text = Field(description="The draft of a reply.")


class RankPostsArgs(ToolArgs):
    topic: Text = Field(description="The account's topic, as the campaign's goal names it.")
    posts: list[Post] = Field(min_length=1, max_length=_MAX_POSTS, description="The posts you could answer.")

    @field_validator("posts")
    @classmethod
    def _distinct_ids(cls, posts: list[Post]) -> list[Post]:
        _check_distinct([post.id for post in posts], "post ids")
        return posts


class PickDraftArgs(ToolArgs):
    topic: Text = Field(description="The account's topic, as the campaign's goal names it.")
    post: Post = Field(description="The post the drafts answer.")
    drafts: list[Draft] = Field(min_length=_MIN_DRAFTS, max_length=_MAX_DRAFTS, description="Your drafts of the reply.")

    @field_validator("drafts")
    @classmethod
    def _distinct_ids(cls, drafts: list[Draft]) -> list[Draft]:
        _check_distinct([draft.id for draft in drafts], "draft ids")
        return drafts


class CheckDraftArgs(ToolArgs):
    topic: Text = Field(description="The account's topic, as the campaign's goal names it.")
    post: Post = Field(description="The post the draft answers.")
    draft: Text = Field(description="The reply about to be posted.")
    earlier_posts: list[Text] = Field(
        max_length=_MAX_EARLIER, description="The account's recent posts and replies, so the draft does not repeat one."
    )
    rules: list[Text] = Field(
        max_length=_MAX_RULES,
        description=(
            "The rules the campaign's goal names for what the account posts ('no links', 'never discuss prices')."
        ),
    )


class Weights(FrozenModel):
    """The weight of each of the three questions, as the campaign's ledger holds them."""

    topic: float
    person: float
    reply: float

    def of(self, name: str) -> float:
        return {"w_topic": self.topic, "w_person": self.person, "w_reply": self.reply}[name]


class RankedPost(FrozenModel):
    """One post's composite score and the probability of yes to each question, by the name of its weight."""

    id: str
    score: float
    answers: dict[str, float]


class DraftCheck(FrozenModel):
    """One check of a draft: what it asked, the probability of yes and whether it fired at the threshold."""

    name: str
    question: str
    probability: float
    fired: bool


def _check_distinct(ids: Sequence[str], what: str) -> None:
    repeated: list[str] = sorted({item for item in ids if ids.count(item) > 1})
    if repeated:
        raise ValueError(f"{what} repeat: {', '.join(repeated)}")


def weights_of(entries: Sequence[CampaignEntry]) -> Weights | None:
    """The weights the newest ledger entry with all three numbers holds, else ``None``."""
    for entry in reversed(entries):
        if all(name in entry.numbers for name in WEIGHT_NAMES):
            return Weights(
                topic=entry.numbers["w_topic"], person=entry.numbers["w_person"], reply=entry.numbers["w_reply"]
            )
    return None


def equal_weights() -> Weights:
    return Weights(topic=_EQUAL_WEIGHT, person=_EQUAL_WEIGHT, reply=_EQUAL_WEIGHT)


def weight_numbers(weights: Weights) -> dict[str, float]:
    return {"w_topic": weights.topic, "w_person": weights.person, "w_reply": weights.reply}


def ranking_event(topic: str, weights: Weights, ranked: Sequence[RankedPost]) -> JsonObject:
    """The ``posts_ranked`` event: the topic, the weights and every post's score and answers, the highest first."""
    scores: list[JsonValue] = [
        {"id": item.id, "score": round(item.score, 4), **{name: value for name, value in item.answers.items()}}
        for item in ranked
    ]
    return {
        "topic": topic,
        "weights": {name: value for name, value in weight_numbers(weights).items()},
        "posts": scores,
    }


def post_batches(posts: Sequence[Post], items_per_request: int) -> tuple[tuple[Post, ...], ...]:
    """``posts`` in groups whose three questions each fit in one request of ``items_per_request`` questions."""
    size: int = max(1, items_per_request // len(_QUESTIONS))
    return tuple(tuple(posts[start : start + size]) for start in range(0, len(posts), size))


def posts_state(topic: str, batch: Sequence[Post]) -> JsonObject:
    return {"topic": topic, "posts": {post.id: {"author": post.author, "text": post.text} for post in batch}}


def question_id(post_id: str, name: str) -> str:
    return f"{post_id}__{name}"


def post_questions(batch: Sequence[Post]) -> tuple[NoulQuestion, ...]:
    """The three questions about every post of ``batch``, each by the post's id and the weight's name."""
    return tuple(
        NoulQuestion(id=question_id(post.id, name), instructions=text.format(path=f"posts.{post.id}"))
        for post in batch
        for name, text in _QUESTIONS
    )


def composite(answers: dict[str, float], weights: Weights) -> float:
    """The weighted mean of the three probabilities; equal weights when they sum to nothing."""
    total: float = sum(weights.of(name) for name in WEIGHT_NAMES)
    if total <= 0:
        return sum(answers[name] for name in WEIGHT_NAMES) / len(WEIGHT_NAMES)
    return sum(answers[name] * weights.of(name) for name in WEIGHT_NAMES) / total


def ranked_posts(batch_answers: dict[str, float], posts: Sequence[Post], weights: Weights) -> tuple[RankedPost, ...]:
    """Every post of ``posts`` scored from the answers of its questions, the highest score first."""
    scored: list[RankedPost] = []
    for post in posts:
        answers: dict[str, float] = {name: batch_answers[question_id(post.id, name)] for name in WEIGHT_NAMES}
        scored.append(RankedPost(id=post.id, score=composite(answers, weights), answers=answers))
    return tuple(sorted(scored, key=lambda item: -item.score))


def _shown(text: str) -> str:
    flat: str = " ".join(text.split())
    return flat if len(flat) <= _SHOWN_CHARS else f"{flat[: _SHOWN_CHARS - 1]}…"


def render_ranking(ranked: Sequence[RankedPost], posts: Sequence[Post], weights: Weights, requests: int) -> str:
    """The ranking as the model reads it: the weights, then one line per post, the highest score first."""
    by_id: dict[str, Post] = {post.id: post for post in posts}
    plural: str = "" if requests == 1 else "s"
    head: str = (
        f"{len(ranked)} posts ranked in {requests} request{plural} by the weighted probability of yes to three "
        f"questions (on the topic ×{weights.topic:g}, by a person ×{weights.person:g}, worth a reply "
        f"×{weights.reply:g}; the weights are in the campaign's ledger), the highest first:"
    )
    lines: list[str] = []
    for rank, item in enumerate(ranked, 1):
        post: Post = by_id[item.id]
        author: str = f" {post.author}:" if post.author else ""
        lines.append(
            f"{rank}. {item.id} {item.score:.2f} (topic {item.answers['w_topic']:.2f}, person "
            f"{item.answers['w_person']:.2f}, reply {item.answers['w_reply']:.2f}){author} {_shown(post.text)}"
        )
    return "\n".join([head, *lines])


def draft_state(args: PickDraftArgs) -> JsonObject:
    return {
        "topic": args.topic,
        "post": {"author": args.post.author, "text": args.post.text},
        "drafts": {draft.id: draft.text for draft in args.drafts},
    }


def draft_choice(args: PickDraftArgs) -> ChoiceQuestion:
    """The one Choice over the drafts: which one is the best reply to the post."""
    return ChoiceQuestion(
        id="best",
        instructions=(
            "Which of the options is the best reply to the post at `post` for an account on the topic in `topic`: it "
            "answers what the post says, adds something (an answer, an example, a fact or a question), stays on the "
            "topic, reads like a person and not a brand, and is short? The options are the drafts in `drafts`, by "
            "their id."
        ),
        options={draft.id: draft.text for draft in args.drafts},
    )


def render_pick(answer: ChoiceAnswer, drafts: Sequence[Draft]) -> str:
    ordered: list[tuple[str, float]] = sorted(answer.probabilities.items(), key=lambda item: -item[1])
    by_id: dict[str, str] = {draft.id: draft.text for draft in drafts}
    lines: list[str] = [
        f"{draft_id} {probability:.2f} — {_shown(by_id[draft_id])}" for draft_id, probability in ordered
    ]
    return "\n".join(
        [
            f"Best draft: {answer.choice} (probability {answer.probabilities[answer.choice]:.2f}, confidence "
            f"{answer.confidence:.2f}). All drafts, the likeliest first:",
            *lines,
        ]
    )


def check_state(args: CheckDraftArgs) -> JsonObject:
    return {
        "topic": args.topic,
        "post": {"author": args.post.author, "text": args.post.text},
        "draft": args.draft,
        "earlier_posts": [*args.earlier_posts],
        "rules": [*args.rules],
    }


def check_questions(args: CheckDraftArgs) -> tuple[NoulQuestion, ...]:
    """Whether the draft is off topic, repeats an earlier post (when there are any) and breaks each rule."""
    questions: list[NoulQuestion] = [
        NoulQuestion(
            id=_OFF_TOPIC,
            instructions=(
                "Is the reply at `draft` off the topic in `topic`, or beside the point of the post at `post` it "
                "answers?"
            ),
        )
    ]
    if args.earlier_posts:
        questions.append(
            NoulQuestion(
                id=_REPEATS,
                instructions=(
                    "Does the reply at `draft` say in substance what one of the account's earlier posts in "
                    "`earlier_posts` already said?"
                ),
            )
        )
    questions.extend(
        NoulQuestion(
            id=f"{_RULE_PREFIX}{number}",
            instructions=f"Does the reply at `draft` break this rule of the campaign: `rules[{number}]`?",
        )
        for number in range(len(args.rules))
    )
    return tuple(questions)


def draft_checks(
    answers: dict[str, float], questions: Sequence[NoulQuestion], rules: Sequence[str], threshold: float
) -> tuple[DraftCheck, ...]:
    """Each check with its probability and whether it fired at ``threshold``."""
    checks: list[DraftCheck] = []
    for question in questions:
        if question.id == _OFF_TOPIC:
            asked: str = "off the topic or beside the point of the post"
        elif question.id == _REPEATS:
            asked = "repeats an earlier post of the account"
        else:
            asked = f"breaks the rule: {rules[int(question.id.removeprefix(_RULE_PREFIX))]}"
        probability: float = answers[question.id]
        checks.append(
            DraftCheck(name=question.id, question=asked, probability=probability, fired=probability >= threshold)
        )
    return tuple(checks)


def render_checks(checks: Sequence[DraftCheck], threshold: float) -> str:
    fired: list[DraftCheck] = [check for check in checks if check.fired]
    lines: list[str] = [
        f"- {check.question}: {check.probability:.2f}{' (fired)' if check.fired else ''}" for check in checks
    ]
    head: str = (
        f"Do not post the draft as it is: {len(fired)} of {len(checks)} checks reached {threshold:.2f}. Change it and "
        "check again, or answer another post."
        if fired
        else f"The draft passes its {len(checks)} checks (none reached {threshold:.2f}); the decision to post is yours."
    )
    return "\n".join([head, *lines])


def _action(tool: str, summary: str, ctx: ToolContext) -> ActionInfo:
    flat: str = " ".join(summary.split())
    return ActionInfo(
        tool=tool,
        kind="network",
        risk="low",
        summary=flat if len(flat) <= _SUMMARY_CHARS else f"{flat[: _SUMMARY_CHARS - 1]}…",
        target=urlsplit(ctx.runtime.config.judge.base_url).netloc,
        flags=(),
    )


def _no_service(ctx: ToolContext) -> ToolResult:
    return fail(
        f"This tool has no TypeSafe service: save the key {ctx.runtime.config.judge.api_key_env} and restart Dot. "
        "Decide yourself."
    )


def _rejected(error: ProviderError) -> ToolResult:
    return fail(
        "TypeSafe rejected the request or answered outside its reference (a judge_error note in the timeline has the "
        f"details): {error.message}. Decide yourself."
    )


def _paused() -> ToolResult:
    return fail(
        "TypeSafe is unavailable for a while (a judge_unavailable note in the timeline says why). Decide yourself."
    )


async def _weights(campaign: CampaignRecord, ctx: ToolContext) -> Weights:
    """The campaign's weights; the first ranking writes equal weights in the ledger, where the owner changes them."""
    entries: list[CampaignEntry] = await ctx.runtime.store.campaign_entries(campaign.id, _MAX_ENTRIES)
    found: Weights | None = weights_of(entries)
    if found is not None:
        return found
    weights: Weights = equal_weights()
    await ctx.runtime.store.add_campaign_entry(campaign.id, ctx.run_id, "note", _WEIGHTS_TEXT, weight_numbers(weights))
    return weights


# How many of the newest ledger entries are read for the weights.
_MAX_ENTRIES: int = 500


def _rank_tool() -> RegisteredTool:
    def classify(args: RankPostsArgs, ctx: ToolContext) -> ActionInfo:
        return _action(RANK_POSTS_TOOL, f"Rank {len(args.posts)} posts with TypeSafe on the topic: {args.topic}", ctx)

    async def run(args: RankPostsArgs, ctx: ToolContext) -> ToolResult:
        service: TypeSafeService | None = ctx.runtime.typesafe
        if service is None:
            return _no_service(ctx)
        campaign: CampaignRecord = await campaign_of(ctx)
        weights: Weights = await _weights(campaign, ctx)
        answers: dict[str, float] = {}
        groups: tuple[tuple[Post, ...], ...] = post_batches(args.posts, service.config.items_per_request)
        try:
            for batch in groups:
                found: dict[str, float] | None = await service.nouls(
                    posts_state(args.topic, batch), post_questions(batch), ctx.run_id
                )
                if found is None:
                    return _paused()
                answers.update(found)
        except ProviderError as error:
            return _rejected(error)
        ranked: tuple[RankedPost, ...] = ranked_posts(answers, args.posts, weights)
        await ctx.runtime.bus.emit(ctx.run_id, POSTS_RANKED_EVENT, ranking_event(args.topic, weights, ranked), True)
        return ok(render_ranking(ranked, args.posts, weights, len(groups)))

    return make_tool(
        name=RANK_POSTS_TOOL,
        description=(
            "Rank the posts you could answer, in one call of a small typed model: for every post the probability "
            "that it is on the account's topic, written by a person (not a bot, a brand or spam) and worth a reply "
            "that adds something, combined with the weights in the campaign's ledger. Answer from the top of the "
            "ranking; the choice stays yours."
        ),
        args_model=RankPostsArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=_TIMEOUT_S,
        classify=classify,
        run=run,
    )


def _pick_tool() -> RegisteredTool:
    def classify(args: PickDraftArgs, ctx: ToolContext) -> ActionInfo:
        return _action(
            PICK_DRAFT_TOOL,
            f"Pick the best of {len(args.drafts)} drafts with TypeSafe for a reply to {args.post.id}",
            ctx,
        )

    async def run(args: PickDraftArgs, ctx: ToolContext) -> ToolResult:
        service: TypeSafeService | None = ctx.runtime.typesafe
        if service is None:
            return _no_service(ctx)
        await campaign_of(ctx)
        question: ChoiceQuestion = draft_choice(args)
        try:
            answers: dict[str, ChoiceAnswer] | None = await service.choices(draft_state(args), (question,), ctx.run_id)
        except ProviderError as error:
            return _rejected(error)
        if answers is None:
            return _paused()
        return ok(render_pick(answers[question.id], args.drafts))

    return make_tool(
        name=PICK_DRAFT_TOOL,
        description=(
            "Pick the best of your drafts of a reply with a small typed model: one choice over the drafts, with the "
            "probability of each (does it answer the post, add something, stay on topic, read like a person, stay "
            "short?). Write two or three drafts first; the choice stays yours."
        ),
        args_model=PickDraftArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=_TIMEOUT_S,
        classify=classify,
        run=run,
    )


def _check_tool() -> RegisteredTool:
    def classify(args: CheckDraftArgs, ctx: ToolContext) -> ActionInfo:
        return _action(CHECK_DRAFT_TOOL, f"Check a draft reply to {args.post.id} with TypeSafe: {args.draft}", ctx)

    async def run(args: CheckDraftArgs, ctx: ToolContext) -> ToolResult:
        service: TypeSafeService | None = ctx.runtime.typesafe
        if service is None:
            return _no_service(ctx)
        await campaign_of(ctx)
        questions: tuple[NoulQuestion, ...] = check_questions(args)
        try:
            answers: dict[str, float] | None = await service.nouls(check_state(args), questions, ctx.run_id)
        except ProviderError as error:
            return _rejected(error)
        if answers is None:
            return _paused()
        threshold: float = ctx.runtime.config.judge.draft_check_threshold
        checks: tuple[DraftCheck, ...] = draft_checks(answers, questions, args.rules, threshold)
        text: str = render_checks(checks, threshold)
        return fail(text) if any(check.fired for check in checks) else ok(text)

    return make_tool(
        name=CHECK_DRAFT_TOOL,
        description=(
            "Check a draft reply before you post it, with a small typed model: is it off topic or beside the point of "
            "the post, does it repeat one of the account's earlier posts, does it break a rule the campaign's goal "
            "names? A check at the threshold refuses the draft with its probabilities; change it and check again."
        ),
        args_model=CheckDraftArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=_TIMEOUT_S,
        classify=classify,
        run=run,
    )


def build_post_tools() -> tuple[RegisteredTool, ...]:
    return (_rank_tool(), _pick_tool(), _check_tool())
