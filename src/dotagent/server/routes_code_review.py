"""The models of the pre-push review of Dot's own repository (item 1663, ``[code_review]``): read by Settings ›
Models › Code review and chosen there. The hook reads the same table through ``dotx review plan``."""

import asyncio

from fastapi import APIRouter
from pydantic import Field

from dotagent.config import CodeReviewConfig, load_user_config
from dotagent.config_edit import set_code_review
from dotagent.core.errors import ConfigError
from dotagent.core.paths import config_path
from dotagent.core.types import FrozenModel
from dotagent.llm.router import ModelRef, parse_model_ref
from dotagent.llm.types import WHOLE_RUN_AGENTS, Effort, ModelInfo, runs_dots_tools
from dotagent.server.context import ServerContext

# How many models the review falls back to at most, in their order.
MAX_REVIEW_FALLBACKS: int = 6


class CodeReviewRequest(FrozenModel):
    """The models of the pre-push review: the review model, its fallbacks in order, the small change's model ("" for
    the review model), the changed lines a small change has at most, and the minutes a push's review may take; the
    efforts of the review model and of the fallbacks when sent (item 1770), kept as they are when not."""

    model: str
    effort: Effort | None = None
    fallback: tuple[str, ...] = Field(max_length=MAX_REVIEW_FALLBACKS)
    fallback_effort: Effort | None = None
    small_model: str
    small_max_lines: int = Field(ge=0, le=100_000)
    max_minutes: int = Field(ge=1, le=240)


def code_review_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api")

    async def reviewer(model: str) -> str:
        """``model`` as its label, once it is known to run a review: a run of Dot's tools (a read-only research run),
        which a model that answers only as an agent program of its own (Zen's free models) cannot be, and one that can
        use actions. A model its provider does not offer is refused by the router."""
        ref: ModelRef = parse_model_ref(model)
        if not runs_dots_tools(ref.provider):
            raise ConfigError(
                f"{ref.label} answers only as {WHOLE_RUN_AGENTS[ref.provider]}, so it cannot review a change; pick "
                "another",
                {"model": model},
            )
        info: ModelInfo = await ctx.runtime.router.model_info(ref)
        if not info.tools:
            raise ConfigError("a review model must be one that can use actions", {"model": ref.label})
        return ref.label

    @router.get("/code-review")
    async def code_review() -> CodeReviewConfig:
        """``[code_review]`` as the config file holds it now: the hook reads the file, not this daemon's start."""
        return (await asyncio.to_thread(load_user_config)).code_review

    @router.put("/code-review")
    async def choose_code_review(request: CodeReviewRequest) -> CodeReviewConfig:
        """Have the next pre-push review run on these models (item 1663): the config file keeps them as
        ``[code_review]``, all written or none. A model named twice, and a model newly named that its provider does not
        offer, that cannot use actions or that answers only as its own agent program are refused, as is a config file
        that cannot be changed; nothing changes then. A phone's key may change them as the Mac's token does (item
        1770, the owner's word): a model is still one its provider offers, that uses actions and runs Dot's tools."""
        current: CodeReviewConfig = (await asyncio.to_thread(load_user_config)).code_review
        # A model the table holds already is kept as it is: a change of the time limit is not refused because the
        # review model's service is out of reach now.
        held: set[str] = {current.model, *current.fallback, current.small_model}

        async def checked(model: str) -> str:
            return model if model in held else await reviewer(model)

        model: str = await checked(request.model)
        fallback: list[str] = [await checked(one) for one in request.fallback]
        small: str = "" if request.small_model == "" else await checked(request.small_model)
        effort: Effort = current.effort if request.effort is None else request.effort
        fallback_effort: Effort = (
            current.fallback_effort if request.fallback_effort is None else request.fallback_effort
        )
        try:
            CodeReviewConfig(
                model=model,
                effort=effort,
                fallback=fallback,
                fallback_effort=fallback_effort,
                small_model=small,
                small_max_lines=request.small_max_lines,
                max_minutes=request.max_minutes,
            )
        except ValueError as exc:
            raise ConfigError(f"the code review cannot be set so: {exc}", {"model": model}) from exc
        await asyncio.to_thread(
            set_code_review,
            config_path(),
            (
                ("model", model),
                ("effort", effort),
                ("fallback", fallback),
                ("fallback_effort", fallback_effort),
                ("small_model", small),
                ("small_max_lines", request.small_max_lines),
                ("max_minutes", request.max_minutes),
            ),
        )
        return (await asyncio.to_thread(load_user_config)).code_review

    return router
