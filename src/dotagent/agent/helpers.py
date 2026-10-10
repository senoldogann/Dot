"""Helper models: the models a run may ask for advice or hand a part of its work to (``[models] helpers``), and the
standby models it moves to when its own model cannot answer (``[models] standby``).

A model is available when its provider has the key it needs (a local Ollama server needs none) and the router has not
paused the model after an error that says it cannot answer for a while (:func:`dotagent.llm.router.pause_for`).
"""

import asyncio
import time
from collections.abc import Collection, Mapping, Sequence

from dotagent.config import ProvidersConfig
from dotagent.core.errors import ConfigError
from dotagent.core.text import until_text
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.router import ModelRouter, Pause, parse_model_ref, unavailable_reason
from dotagent.secrets import secret_state

# A helper model could not answer the question a run asked it.
HELPER_UNAVAILABLE_EVENT: str = "helper_unavailable"
# A run moved to a standby model because its own model could not answer.
MODEL_SWITCHED_EVENT: str = "model_switched"
_NO_KEY: str = "no key"


class Unavailable(FrozenModel):
    """A model that cannot be used now: why, and until when (seconds since the epoch) when it is paused."""

    model: str
    reason: str
    until: float | None


class Roster(FrozenModel):
    """Configured models in their order (as ``provider:model`` labels, each once), split into the ones that are
    available now and the others."""

    available: tuple[str, ...]
    unavailable: tuple[Unavailable, ...]


def roster_for(models: Sequence[str], keyed: Collection[str], paused: Mapping[str, Pause]) -> Roster:
    """Split ``models``: a model is available when its provider is in ``keyed`` and the model is not in ``paused``
    (by its label)."""
    available: list[str] = []
    unavailable: list[Unavailable] = []
    for label in dict.fromkeys(parse_model_ref(model).label for model in models):
        provider: str = parse_model_ref(label).provider
        pause: Pause | None = paused.get(label)
        if provider not in keyed:
            unavailable.append(Unavailable(model=label, reason=_NO_KEY, until=None))
        elif pause is not None:
            unavailable.append(Unavailable(model=label, reason=pause.reason, until=pause.until))
        else:
            available.append(label)
    return Roster(available=tuple(available), unavailable=tuple(unavailable))


def _key_name(providers: ProvidersConfig, provider: str) -> str | None:
    """The API key that ``provider`` cannot answer without; ``None`` for Ollama, whose local server needs none."""
    match provider:
        case "ollama":
            return None
        case "openai":
            return providers.openai.api_key_env
        case "opencode_go":
            return providers.opencode_go.api_key_env
        case "anthropic":
            return providers.anthropic.api_key_env
        case _:
            raise ConfigError("unknown provider", {"provider": provider})


def _has_key(providers: ProvidersConfig, provider: str) -> bool:
    key: str | None = _key_name(providers, provider)
    return key is None or secret_state(key).source is not None


def _keyed(providers: ProvidersConfig, names: frozenset[str]) -> frozenset[str]:
    """Blocking: the Keychain is read by a subprocess when neither the environment nor the env file has a key."""
    return frozenset(name for name in names if _has_key(providers, name))


async def keyed_providers(providers: ProvidersConfig, models: Sequence[str]) -> frozenset[str]:
    """The providers of ``models`` that have the key they need, from wherever Dot reads keys."""
    names: frozenset[str] = frozenset(parse_model_ref(model).provider for model in models)
    if not names:
        return names
    return await asyncio.to_thread(_keyed, providers, names)


async def current_roster(router: ModelRouter, providers: ProvidersConfig, models: Sequence[str]) -> Roster:
    """``models`` as they stand now: with the keys of their providers and the pauses of ``router``."""
    keyed: frozenset[str] = await keyed_providers(providers, models)
    return roster_for(models, keyed, router.paused(time.time()))


def unavailable_after(router: ModelRouter, model: str, error: Exception) -> Unavailable | None:
    """What ``error``, raised by a call to ``model`` (a label), made of the model: unavailable until the end of its
    pause, or ``None`` when the error says nothing about the model being unable to answer."""
    reason: str | None = unavailable_reason(error)
    if reason is None:
        return None
    pause: Pause | None = router.paused(time.time()).get(model)
    return Unavailable(model=model, reason=reason, until=None if pause is None else pause.until)


def unavailable_text(entry: Unavailable, now: float) -> str:
    """``anthropic:claude-opus-5-5 (quota exhausted until 14:05)``"""
    if entry.until is None:
        return f"{entry.model} ({entry.reason})"
    return f"{entry.model} ({entry.reason} until {until_text(entry.until, now)})"


def roster_text(roster: Roster, now: float) -> str:
    """The models of ``roster`` for a tool result: the available ones, then the others with why."""
    available: str = ", ".join(roster.available) if roster.available else "none"
    if not roster.unavailable:
        return f"available: {available}"
    others: str = ", ".join(unavailable_text(entry, now) for entry in roster.unavailable)
    return f"available: {available}; cannot answer now: {others}"


def unavailable_event_data(entry: Unavailable) -> JsonObject:
    """The ``helper_unavailable`` event of a helper that could not answer."""
    return {"model": entry.model, "reason": entry.reason, "until": entry.until}
