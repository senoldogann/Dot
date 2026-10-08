"""The scaffold of a run: how much help its model gets, from facts and not from one global setting.

The budget follows the model's window: a run never plans to fill more than three quarters of what one request can hold,
so a model with a small window is compacted before it overflows, and its tool output is clipped in the same proportion.
The tier follows how reliably the model worked in its newest runs: a model whose tool calls are often refused before
anything runs (an unknown tool, arguments that were not JSON or did not fit) is ``weak`` and is reminded of its goal
every few steps; every other model is ``standard``. A tier can be set by hand, and is ``standard`` for every model while
``[agent] scaffold`` is ``fixed``.

Everything here is pure; the loop reads the window and the profile and acts on the scaffold.
"""

from dotagent.config import AgentConfig, Tier
from dotagent.core.errors import ConfigError
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.types import ModelInfo
from dotagent.store.models import RunMode
from dotagent.store.stats import ModelProfile

# The newest finished runs a model's profile is read from.
PROFILE_RUNS: int = 30
# The share of the window a prompt may fill; the rest is room for the reply and for the error of the token estimate.
_WINDOW_SHARE: float = 0.75
# Tool output is never clipped below this, however small the window.
_MIN_OUTPUT_CHARS: int = 2000


class Scaffold(FrozenModel):
    """The help one run of a model gets. ``window`` is what one request of the model can hold (``None`` when its provider
    does not say); ``context_tokens`` and ``tool_output_chars`` are the run's budgets, never above the configured ones;
    ``reason`` says why the model has its tier; ``reminder_every`` is the number of steps between two reminders of the
    goal, ``None`` when there are none."""

    model: str
    window: int | None
    context_tokens: int
    tool_output_chars: int
    tier: Tier
    reason: str
    reminder_every: int | None


def context_budget(config: AgentConfig, window: int | None) -> int:
    """``[agent] context_tokens``, or three quarters of the window when that is less."""
    return config.context_tokens if window is None else min(config.context_tokens, round(window * _WINDOW_SHARE))


def output_budget(config: AgentConfig, context_tokens: int) -> int:
    """``[agent] tool_output_chars``, shrunk in the proportion the window cut the context budget."""
    scaled: int = round(config.tool_output_chars * context_tokens / config.context_tokens)
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


def scaffold_for(config: AgentConfig, info: ModelInfo, profile: ModelProfile) -> Scaffold:
    """The scaffold of a run of the profile's model, whose provider describes it with ``info``."""
    context_tokens: int = context_budget(config, info.context_length)
    tier, reason = choose_tier(config, profile)
    return Scaffold(
        model=profile.model,
        window=info.context_length,
        context_tokens=context_tokens,
        tool_output_chars=output_budget(config, context_tokens),
        tier=tier,
        reason=reason,
        reminder_every=config.reminder_every if tier == "weak" else None,
    )


def scaffold_event(scaffold: Scaffold, config: AgentConfig) -> JsonObject:
    """The ``scaffold`` event of a run. ``cut_to_window`` says that the window made the budget smaller than
    ``[agent] context_tokens``."""
    return {
        "model": scaffold.model,
        "window": scaffold.window,
        "context_tokens": scaffold.context_tokens,
        "tool_output_chars": scaffold.tool_output_chars,
        "cut_to_window": scaffold.context_tokens < config.context_tokens,
        "tier": scaffold.tier,
        "reason": scaffold.reason,
        "reminder_every": scaffold.reminder_every,
    }


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
                "hint": "pick a model with a larger window, raise [providers.ollama] num_ctx for an Ollama model, "
                "or raise [agent] context_tokens when it is below three quarters of the window",
            },
        )
