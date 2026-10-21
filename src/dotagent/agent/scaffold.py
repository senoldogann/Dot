"""The scaffold of a run: how much help its model gets, from facts and not from one global setting.

The budget is the context size chosen for the model in Settings › Models (item 250), else ``[agent] context_tokens``:
``"most"``, three quarters of the model's window (item 396), or one number for every model. It follows the model's
window: a run never plans to fill more than three quarters of what one request can hold, so a model with a small window
is compacted before it overflows, and its tool output is clipped in the same proportion.
The tier follows how reliably the model worked in its newest runs: a model whose tool calls are often refused before
anything runs (an unknown tool, arguments that were not JSON or did not fit) is ``weak`` and is reminded of its goal
every few steps; every other model is ``standard``. A tier can be set by hand, and is ``standard`` for every model while
``[agent] scaffold`` is ``fixed``. The tool surface follows ``[agent] tool_surface``: with ``auto`` a model of the weak
tier, and one whose window cut the budget, start with the core tools and turn the other tool groups on when they need
them.

Everything here is pure; the loop reads the window and the profile and acts on the scaffold.
"""

from typing import Literal

from dotagent.config import CONTEXT_FLOOR, AgentConfig, Tier
from dotagent.core.errors import ConfigError
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.types import WINDOW_SHARE, ModelInfo, grown_window
from dotagent.store.models import RunMode
from dotagent.store.stats import ModelProfile

# The newest finished runs a model's profile is read from.
PROFILE_RUNS: int = 30
# Tool output is never clipped below this, however small the window.
_MIN_OUTPUT_CHARS: int = 2000
# The context sizes Settings › Models offers for a model (item 250), up to three quarters of its window.
_SIZE_STEPS: tuple[int, ...] = (48_000, 64_000, 96_000, 128_000, 192_000, 256_000, 384_000, 512_000, 768_000)
# While [agent] context_tokens is "most" (item 396): the budget [agent] tool_output_chars is meant for, under which the
# tool surface counts a budget as small, and the size of a model whose provider does not say its window. It was the
# default budget of every run from 2026-10-11 until item 396: in a run of 41 steps measured then, 48,000 summarized
# twice and 96,000 not once.
_REFERENCE_TOKENS: int = 96_000

# How a run offers its tools: all of them at every step, or the core tools and the groups that are on.
type Surface = Literal["full", "grouped"]


class Scaffold(FrozenModel):
    """The help one run of a model gets. ``window`` is what one request of the model can hold (``None`` when its
    provider does not say); ``chosen`` is the context size chosen for the model (item 250), ``None`` when its runs
    have the one ``[agent] context_tokens`` gives; ``context_tokens`` and ``tool_output_chars`` are the run's budgets,
    never above the configured ones; ``reason`` says why the model has its tier; ``reminder_every`` is the number of
    steps between two reminders of the goal, ``None`` when there are none."""

    model: str
    window: int | None
    chosen: int | None
    context_tokens: int
    tool_output_chars: int
    tier: Tier
    reason: str
    reminder_every: int | None


def _reference_size(config: AgentConfig) -> int:
    """The budget ``[agent] tool_output_chars`` is meant for, under which the tool surface counts a run's budget as
    small: the number ``[agent] context_tokens`` sets, or 96,000 while it is ``"most"``."""
    return config.context_tokens if isinstance(config.context_tokens, int) else _REFERENCE_TOKENS


def configured_size(config: AgentConfig, window: int | None, chosen: int | None) -> int:
    """The context size a model whose request holds ``window`` tokens is configured with: the one chosen for the model
    (item 250), else the number ``[agent] context_tokens`` sets. While that is ``"most"`` (item 396), three quarters of
    the window, never less than 96,000: so a window too small for that cuts the budget as it cuts a number, and a model
    whose provider does not say its window has 96,000."""
    if chosen is not None:
        return chosen
    if isinstance(config.context_tokens, int):
        return config.context_tokens
    ceiling: int | None = context_ceiling(window)
    return _REFERENCE_TOKENS if ceiling is None else max(ceiling, _REFERENCE_TOKENS)


def context_ceiling(window: int | None) -> int | None:
    """The most a run of a model whose request holds ``window`` tokens plans to fill: three quarters of the window,
    ``None`` when its provider does not say."""
    return None if window is None else round(window * WINDOW_SHARE)


def context_budget(config: AgentConfig, window: int | None, chosen: int | None) -> int:
    """The configured size (:func:`configured_size`), or three quarters of the window when that is less."""
    size: int = configured_size(config, window, chosen)
    ceiling: int | None = context_ceiling(window)
    return size if ceiling is None else min(size, ceiling)


def context_choices(config: AgentConfig, window: int | None, chosen: int | None) -> tuple[int, ...]:
    """The context sizes Settings › Models offers for a model besides the default one (item 250), smallest first: the
    steps from :data:`CONTEXT_FLOOR` up to three quarters of its window, that ceiling itself, and the size chosen now.
    None when its provider does not say its window, or when the ceiling is below the floor."""
    return _offered(context_ceiling(window), context_budget(config, window, None), chosen)


def _offered(ceiling: int | None, default: int, chosen: int | None) -> tuple[int, ...]:
    """The sizes from :data:`CONTEXT_FLOOR` up to ``ceiling``, that ceiling itself and ``chosen``, smallest first,
    without the ``default`` one; none when there is no ceiling or it is below the floor."""
    if ceiling is None or ceiling < CONTEXT_FLOOR:
        return ()
    steps: set[int] = {size for size in (*_SIZE_STEPS, ceiling) if CONTEXT_FLOOR <= size <= ceiling and size != default}
    return tuple(sorted(steps if chosen is None else steps | {chosen}))


def window_choices(config: AgentConfig, info: ModelInfo, chosen: int | None) -> tuple[int, ...]:
    """The context sizes Settings › Models offers for the model ``info`` describes besides its default one (item 250),
    as :func:`context_choices` gives them, up to three quarters of the most window it may be given: its own for an
    Ollama model whose own window is larger than the one Dot asks for by default, which a larger size asks for (item
    520)."""
    return _offered(context_ceiling(most_window(info)), context_budget(config, info.context_length, None), chosen)


def most_window(info: ModelInfo) -> int | None:
    """The most window the model ``info`` describes may be given: its own where it holds more than its provider is asked
    for by default (an Ollama model, item 520), else the one its provider says."""
    return info.context_length if info.own_context_length is None else info.own_context_length


def summarise_point(config: AgentConfig, window: int | None, chosen: int | None, context_tokens: int) -> float:
    """The estimated prompt past which a run with a budget of ``context_tokens`` summarises its older part (item 87):
    ``context_summarise_share`` of its configured size (:func:`configured_size`), or its whole budget when a small
    window cut it under that, since a budget that small has no share to spare."""
    return min(configured_size(config, window, chosen) * config.context_summarise_share, context_tokens)


def output_budget(config: AgentConfig, context_tokens: int) -> int:
    """``[agent] tool_output_chars``, shrunk in the proportion the context budget is below :func:`_reference_size` (a
    window that cut it, or a smaller size chosen for the model); never grown."""
    scaled: int = round(config.tool_output_chars * context_tokens / _reference_size(config))
    return min(config.tool_output_chars, max(_MIN_OUTPUT_CHARS, scaled))


def choose_tier(config: AgentConfig, profile: ModelProfile) -> tuple[Tier, str]:
    """The tier of the profile's model and the reason for it."""
    chosen: Tier | None = config.tiers.get(profile.model)
    if chosen is not None:
        return chosen, "set by hand in [agent] tiers"
    if config.scaffold == "fixed":
        return "standard", '[agent] scaffold is "fixed"'
    needed: int = max(config.profile_min_calls, 1)
    if profile.tool_calls < needed:
        return "standard", f"{profile.tool_calls} tool calls measured, {needed} needed for a judgement"
    share: float = profile.refused_calls / profile.tool_calls
    weak: bool = share >= config.weak_refusal_share
    return ("weak" if weak else "standard"), (
        f"{profile.refused_calls} of {profile.tool_calls} tool calls in the newest {profile.runs} runs were refused "
        f"before they ran ({share:.1%}), {'at or above' if weak else 'below'} {config.weak_refusal_share:.0%}"
    )


def run_window(info: ModelInfo, chosen: int | None) -> int | None:
    """The window a run of the model ``info`` describes asks for, with ``chosen`` its context size: its provider's, or
    for a model that holds more than its provider is asked for by default (an Ollama model of this Mac, item 520) as
    much of its own window as the size needs (:func:`~dotagent.llm.types.grown_window`)."""
    return grown_window(info.context_length, info.own_context_length, chosen)


def scaffold_for(config: AgentConfig, info: ModelInfo, profile: ModelProfile, chosen: int | None) -> Scaffold:
    """The scaffold of a run of the profile's model, whose provider describes it with ``info`` and whose context size
    is ``chosen`` (``None``: the one ``[agent] context_tokens`` gives)."""
    window: int | None = run_window(info, chosen)
    context_tokens: int = context_budget(config, window, chosen)
    tier, reason = choose_tier(config, profile)
    return Scaffold(
        model=profile.model,
        window=window,
        chosen=chosen,
        context_tokens=context_tokens,
        tool_output_chars=output_budget(config, context_tokens),
        tier=tier,
        reason=reason,
        reminder_every=config.reminder_every if tier == "weak" else None,
    )


def scaffold_event(scaffold: Scaffold, config: AgentConfig) -> JsonObject:
    """The ``scaffold`` event of a run. ``cut_to_window`` says that the window made the budget smaller than the
    configured size; ``chosen`` is the context size chosen for the model (item 250), ``summarise_at`` the estimated
    prompt past which the run summarises its older part."""
    return {
        "model": scaffold.model,
        "window": scaffold.window,
        "chosen": scaffold.chosen,
        "context_tokens": scaffold.context_tokens,
        "summarise_at": round(summarise_point(config, scaffold.window, scaffold.chosen, scaffold.context_tokens)),
        "tool_output_chars": scaffold.tool_output_chars,
        "cut_to_window": scaffold.context_tokens < configured_size(config, scaffold.window, scaffold.chosen),
        "tier": scaffold.tier,
        "reason": scaffold.reason,
        "reminder_every": scaffold.reminder_every,
    }


def surface_for(config: AgentConfig, scaffold: Scaffold) -> tuple[Surface, str]:
    """How a run with ``scaffold`` offers its tools, and why."""
    if config.tool_surface == "full":
        return "full", '[agent] tool_surface is "full"'
    if config.tool_surface == "grouped":
        return "grouped", '[agent] tool_surface is "grouped"'
    if scaffold.tier == "weak":
        return "grouped", '[agent] tool_surface is "auto" and the model is in the weak tier'
    reference: int = _reference_size(config)
    if scaffold.context_tokens < reference and scaffold.context_tokens < configured_size(
        config, scaffold.window, scaffold.chosen
    ):
        return "grouped", (
            f'[agent] tool_surface is "auto" and the model\'s window cut the context budget to '
            f"{scaffold.context_tokens:,} tokens"
        )
    if scaffold.context_tokens < reference:
        return "grouped", (
            f'[agent] tool_surface is "auto" and the context size chosen for the model, {scaffold.context_tokens:,} '
            f"tokens, is below {reference:,}"
        )
    return "full", '[agent] tool_surface is "auto", the model is standard and its window holds the whole budget'


def reminder_due(scaffold: Scaffold, mode: RunMode, steps: int, reminded_at: int) -> bool:
    """Whether a task or research run that is at step ``steps``, and was last reminded at step ``reminded_at`` (0 for
    never), is reminded of its goal now."""
    return scaffold.reminder_every is not None and mode != "chat" and steps - reminded_at >= scaffold.reminder_every


def require_room(scaffold: Scaffold, fixed_tokens: int) -> None:
    """Refuse a run whose system prompt and tool definitions (``fixed_tokens``) alone fill its budget: it would compact
    at every step and still not fit."""
    if fixed_tokens >= scaffold.context_tokens:
        raise ConfigError(
            "the model's window is too small for the system prompt and the tool definitions",
            {
                "model": scaffold.model,
                "window": scaffold.window,
                "context_tokens": scaffold.context_tokens,
                "fixed_tokens": fixed_tokens,
                "hint": (
                    "give the model a larger context size in Settings › Models (an Ollama model of this Mac is then "
                    "asked for the window it needs, up to its own), or pick a model with a larger window"
                ),
            },
        )
