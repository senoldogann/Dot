"""Why a run's model calls were read from the provider's cache or were not (item 1742).

A provider reads a prompt back from its cache while the prompt starts with what an earlier call sent, and the earlier
call is not older than the provider keeps it. On the owner's runs of 2026-11-10 to 11-24, 94 % of the input was read
from the cache, and the 6 % that was not came from three places: a chat's first step after a pause (cold, whatever the
prompt), a step that turned a tool group on (the tools stand before the conversation, so the step is cold), and a
prompt that changed between two runs of one chat. :func:`cache_report` names which of them a run met, from what the run
recorded of itself, so that a cold first step is a cause and not a guess.

Pure: the store layer reads the calls, the marks and the parts, and this module only counts."""

from collections.abc import Sequence
from typing import Literal

from dotagent.agent.context_parts import ContextParts
from dotagent.core.types import FrozenModel
from dotagent.llm.types import CacheTtl

# A call that read less than this share of its input from the cache did not find its prompt there.
COLD_SHARE: float = 0.2
# A later step that sent less than this many tokens the provider had not read before is a step that grew by a tool
# result, not one that met a cold cache.
COLD_STEP_MIN_TOKENS: int = 20_000
# A chat that was last called between these two moments ago writes its cache for an hour (:func:`cache_ttl_for`): the
# five minutes every provider keeps one for would have run out, and an hour would not have, with a margin for the call.
SHORT_TTL_S: float = 300.0
LONG_TTL_MAX_GAP_S: float = 3300.0

FirstCause = Literal["warm", "new_chat", "prefix_changed", "after_pause", "unexplained"]
ColdCause = Literal["tool_group", "compaction", "other"]
MarkKind = Literal["tool_group", "compaction"]


def cache_ttl_for(gap_s: float | None) -> CacheTtl:
    """How long a run writes its cache when its chat was last called ``gap_s`` seconds ago (``None``: never). A pause
    of five minutes to just under an hour is the pause the provider's five minutes lose and an hour keeps, so a chat or
    a schedule that comes back at that rhythm reads its prompt from the cache each time; a chat that is new, busy or
    away for longer gets the provider's own (an hour's write costs more, and would not be read)."""
    if gap_s is not None and SHORT_TTL_S < gap_s <= LONG_TTL_MAX_GAP_S:
        return "1h"
    return "5m"


class StepCall(FrozenModel):
    """One step call of a run that answered: when it ended, its seconds and its tokens."""

    ts: float
    seconds: float
    input_tokens: int
    cached_tokens: int
    cache_write_tokens: int


class Mark(FrozenModel):
    """A moment of the run that changes what the next call sends before the conversation (a tool group turned on) or
    inside it (a compaction)."""

    kind: MarkKind
    ts: float


class ColdStep(FrozenModel):
    """A step after the first that sent ``uncached_tokens`` the provider read at full price, with what happened just
    before it."""

    step: int
    uncached_tokens: int
    cause: ColdCause


class CacheReport(FrozenModel):
    """What the provider's cache did for one run. ``share`` is the part of the input read from it (``None`` for a run
    with no measured step); ``first_share`` the same for the first step, with ``first_cause`` and, when the prompt
    changed from the chat's previous run, ``changed`` (the sections, ``system:<heading>``, and the tool groups,
    ``tools:<group>``, that differ); ``gap_s`` is how long ago the chat was last called. ``cold_steps`` are the later
    steps that met a cold cache."""

    steps: int
    input_tokens: int
    cached_tokens: int
    cache_write_tokens: int
    share: float | None
    first_share: float | None
    first_cause: FirstCause
    gap_s: float | None
    changed: tuple[str, ...]
    cold_steps: tuple[ColdStep, ...]


def _differing(now: dict[str, str], before: dict[str, str], prefix: str) -> list[str]:
    return sorted(f"{prefix}:{name}" for name in {*now, *before} if now.get(name) != before.get(name))


def _differing_sizes(now: dict[str, int], before: dict[str, int], prefix: str) -> list[str]:
    return sorted(f"{prefix}:{name}" for name in {*now, *before} if now.get(name) != before.get(name))


def changed_parts(now: ContextParts, before: ContextParts) -> tuple[str, ...]:
    """The sections of the system prompt and the tool groups of ``now`` that differ from ``before``'s. By their
    fingerprints when both runs recorded them, else by their sizes, which miss a change that kept a part's size."""
    if now.system_hashes and before.system_hashes:
        return tuple(
            [
                *_differing(now.system_hashes, before.system_hashes, "system"),
                *_differing(now.tools_hashes, before.tools_hashes, "tools"),
            ]
        )
    return tuple(
        [
            *_differing_sizes(now.system, before.system, "system"),
            *_differing_sizes(now.tools, before.tools, "tools"),
        ]
    )


def _cold(call: StepCall) -> bool:
    return call.cached_tokens < COLD_SHARE * call.input_tokens


def _first_cause(first: StepCall, gap_s: float | None, changed: tuple[str, ...]) -> FirstCause:
    if not _cold(first):
        return "warm"
    if gap_s is None:
        return "new_chat"
    if changed:
        return "prefix_changed"
    if gap_s > SHORT_TTL_S:
        return "after_pause"
    # A call a few minutes after the last, the same prompt, and still cold: the provider's cache did not hold.
    return "unexplained"


def _cold_cause(call: StepCall, before: StepCall, marks: Sequence[Mark]) -> ColdCause:
    """What happened between the end of ``before`` and the start of ``call``."""
    started: float = call.ts - call.seconds
    between: list[Mark] = [mark for mark in marks if before.ts - 1.0 <= mark.ts <= started + 1.0]
    if any(mark.kind == "tool_group" for mark in between):
        return "tool_group"
    if any(mark.kind == "compaction" for mark in between):
        return "compaction"
    return "other"


def cache_report(
    calls: Sequence[StepCall],
    marks: Sequence[Mark],
    parts: ContextParts | None,
    before: ContextParts | None,
    gap_s: float | None,
) -> CacheReport | None:
    """The report of a run from its step calls in order, its marks, its parts and the parts of its chat's previous run
    (``None`` for none), and ``gap_s``, how long before its first call the chat was last called. ``None`` for a run
    that measured no step."""
    if not calls:
        return None
    first: StepCall = calls[0]
    changed: tuple[str, ...] = () if parts is None or before is None else changed_parts(parts, before)
    total: int = sum(call.input_tokens for call in calls)
    cached: int = sum(call.cached_tokens for call in calls)
    cold_steps: list[ColdStep] = [
        ColdStep(
            step=index + 2,
            uncached_tokens=call.input_tokens - call.cached_tokens,
            cause=_cold_cause(call, calls[index], marks),
        )
        for index, call in enumerate(calls[1:])
        if _cold(call) and call.input_tokens - call.cached_tokens > COLD_STEP_MIN_TOKENS
    ]
    return CacheReport(
        steps=len(calls),
        input_tokens=total,
        cached_tokens=cached,
        cache_write_tokens=sum(call.cache_write_tokens for call in calls),
        share=None if total == 0 else cached / total,
        first_share=None if first.input_tokens == 0 else first.cached_tokens / first.input_tokens,
        first_cause=_first_cause(first, gap_s, changed),
        gap_s=gap_s,
        changed=changed,
        cold_steps=tuple(cold_steps),
    )
