"""A call to a hook, as the daemon takes it (``POST /api/hooks/{id}``): checked first, from the file as it stands
(:func:`dotagent.hooks.model.admission`); a call that is refused there costs a read and nothing else, never a write of
the file nor a call of the hook's limit, so that one who knows the address and not the secret neither fills the disk,
pushes the real calls out of the list, uses up the hook's calls a minute, nor learns from a 429 that the hook exists.
A call that passed is counted against the hook's limit, checked again and its signature remembered in one locked
change of the file, so two copies of one call cannot both pass, then the bound work starts and the call is written to
the hook's list.

The caller is told the status alone, and for every refused signature the same words; why it was refused is the owner's
to read, in Settings › Hooks (the refusals kept in the daemon's memory, :class:`Refusals`) and the log, where at most
:data:`LOGGED_REFUSALS` a minute are written. Neither the body nor the signature is logged."""

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from pathlib import Path

from dotagent.core.errors import ConfigError, DotError
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.hooks.book import change_hooks, found_hook, read_hooks, replaced
from dotagent.hooks.model import (
    KEPT_CALLS,
    Admission,
    Hook,
    HookCall,
    HookFile,
    admission,
    is_hook_id,
    parsed_body,
    sent_signature,
    with_call,
    with_seen,
)

log = get_logger(__name__)

_MINUTE_S: float = 60.0
_REFUSED: str = "the call was refused"
_UNAUTHORIZED: int = 401
_TOO_MANY: int = 429
_NOT_JSON: int = 422
_STARTED: int = 202
_UNAVAILABLE: int = 503
# The refused calls written to the log in a minute, across every hook; the rest are counted in memory alone.
LOGGED_REFUSALS: int = 60

# What starts a hook's work for a call that passed, with the body read: the id of the run it started.
type StartHook = Callable[[Hook, JsonObject], Awaitable[str]]


class CallAnswer(FrozenModel):
    """What the caller of a hook is answered: the status, a few words, and the run it started."""

    status: int
    message: str
    run_id: str | None


class CallCounter:
    """How many signed calls each hook took in the last minute, in the daemon's memory: a call past the hook's own
    number is refused (429). Only a call whose signature matched is counted, so that no one without the secret uses
    the hook's calls up. A restart starts the count afresh."""

    def __init__(self) -> None:
        self._times: dict[str, deque[float]] = {}

    def take(self, hook_id: str, per_minute: int, now: float) -> bool:
        """Count a call to ``hook_id`` at ``now``; ``False`` when ``per_minute`` calls came in the minute before it."""
        times: deque[float] = self._times.setdefault(hook_id, deque())
        while times and now - times[0] >= _MINUTE_S:
            times.popleft()
        if len(times) >= per_minute:
            return False
        times.append(now)
        return True

    def forget(self, kept: frozenset[str]) -> None:
        """Let go of the counts of hooks no longer kept."""
        for hook_id in [hook_id for hook_id in self._times if hook_id not in kept]:
            del self._times[hook_id]


class Refusals:
    """The calls refused before their signature matched, in the daemon's memory alone: the newest :data:`KEPT_CALLS`
    of each hook that exists, for Settings › Hooks, and the log written for at most :data:`LOGGED_REFUSALS` of them a
    minute, so a flood of them writes neither the file nor an endless log."""

    def __init__(self) -> None:
        self._kept: dict[str, deque[HookCall]] = {}
        self._logged: deque[float] = deque()

    def note(self, hook_id: str | None, call: HookCall) -> None:
        """Keep ``call``, refused, for the hook ``hook_id`` (``None``: no such hook, which is logged alone)."""
        if hook_id is not None:
            self._kept.setdefault(hook_id, deque(maxlen=KEPT_CALLS)).append(call)
        if self.may_log(call.at):
            log.info("hook_call_refused", hook_id=hook_id, reason=call.reason)

    def may_log(self, now: float) -> bool:
        """Whether a line of a call no signature let in may be logged at ``now``: :data:`LOGGED_REFUSALS` a minute."""
        while self._logged and now - self._logged[0] >= _MINUTE_S:
            self._logged.popleft()
        if len(self._logged) >= LOGGED_REFUSALS:
            return False
        self._logged.append(now)
        return True

    def of(self, hook_id: str) -> tuple[HookCall, ...]:
        """The refusals kept of ``hook_id``, the oldest first."""
        return tuple(self._kept.get(hook_id, ()))

    def forget(self, kept: frozenset[str]) -> None:
        for hook_id in [hook_id for hook_id in self._kept if hook_id not in kept]:
            del self._kept[hook_id]


def _admitting(
    hook_id: str, timestamp: str | None, signature: str | None, body: bytes, now: float
) -> Callable[[HookFile], tuple[HookFile, Admission]]:
    """The change of the file that checks a call again under the lock and, when it passes, remembers its signature; a
    refusal changes nothing (the hook went, was switched off or rotated, or a copy of the call passed first)."""

    def change(book: HookFile) -> tuple[HookFile, Admission]:
        hook: Hook | None = found_hook(book, hook_id)
        if hook is None:
            return book, Admission(status=_UNAUTHORIZED, reason="no such hook")
        verdict: Admission = admission(hook, timestamp, signature, body, now)
        sent: str | None = sent_signature(signature)
        if verdict.status != 0 or sent is None:
            return book, verdict
        return replaced(book, with_seen(hook, sent, now)), verdict

    return change


def _recording(hook_id: str, call: HookCall) -> Callable[[HookFile], tuple[HookFile, None]]:
    def change(book: HookFile) -> tuple[HookFile, None]:
        hook: Hook | None = found_hook(book, hook_id)
        return (book if hook is None else replaced(book, with_call(hook, call))), None

    return change


async def _record(path: Path, hook_id: str, call: HookCall) -> None:
    await asyncio.to_thread(change_hooks, path, _recording(hook_id, call))
    log.info("hook_called", hook_id=hook_id, status=call.status, reason=call.reason, run_id=call.run_id)


def _refused(refusals: Refusals, hook_id: str | None, reason: str, now: float) -> CallAnswer:
    refusals.note(hook_id, HookCall(at=now, status=_UNAUTHORIZED, reason=reason, run_id=None))
    return CallAnswer(status=_UNAUTHORIZED, message=_REFUSED, run_id=None)


async def take_call(
    path: Path,
    hook_id: str,
    timestamp: str | None,
    signature: str | None,
    body: bytes,
    now: float,
    counter: CallCounter,
    refusals: Refusals,
    start: StartHook,
    status_of: Callable[[DotError], int],
) -> CallAnswer:
    """Take a call to the hook ``hook_id`` at ``now``: refuse it (401 for an unknown hook and every failed check, the
    same to the caller; 429 past the hook's limit; 422 for a body that is no JSON object) or start its work with
    ``start``; a start that fails is answered with ``status_of`` its error. A hooks file that cannot be read is 503 to
    the caller, its path and error in the log alone. Only a call whose signature matched is written to the file."""
    if not is_hook_id(hook_id):
        return _refused(refusals, None, "no such hook", now)
    try:
        book: HookFile = await asyncio.to_thread(read_hooks, path)
    except ConfigError as error:
        # Any caller of a hook's address reaches this before a signature is checked: logged within the same limit.
        if refusals.may_log(now):
            log.error("hooks_file_unreadable", error=error.message, context=error.context)
        return CallAnswer(status=_UNAVAILABLE, message="hooks are not available right now", run_id=None)
    hook: Hook | None = found_hook(book, hook_id)
    if hook is None:
        return _refused(refusals, None, "no such hook", now)
    checked: Admission = admission(hook, timestamp, signature, body, now)
    if checked.status != 0:
        return _refused(refusals, hook.id, checked.reason, now)
    if not counter.take(hook.id, hook.per_minute, now):
        await _record(path, hook.id, HookCall(at=now, status=_TOO_MANY, reason="past its calls a minute", run_id=None))
        return CallAnswer(status=_TOO_MANY, message="too many calls: try again in a minute", run_id=None)
    verdict: Admission = await asyncio.to_thread(
        change_hooks, path, _admitting(hook.id, timestamp, signature, body, now)
    )
    if verdict.status != 0:
        return _refused(refusals, hook.id, verdict.reason, now)
    payload: JsonObject | None = parsed_body(body)
    if payload is None:
        await _record(
            path, hook.id, HookCall(at=now, status=_NOT_JSON, reason="the body is no JSON object", run_id=None)
        )
        return CallAnswer(status=_NOT_JSON, message="the body must be a JSON object", run_id=None)
    try:
        run_id: str = await start(hook, payload)
    except DotError as error:
        status: int = status_of(error)
        await _record(path, hook.id, HookCall(at=now, status=status, reason=error.message, run_id=None))
        return CallAnswer(status=status, message=error.message, run_id=None)
    await _record(path, hook.id, HookCall(at=now, status=_STARTED, reason="started", run_id=run_id))
    return CallAnswer(status=_STARTED, message="started", run_id=run_id)
