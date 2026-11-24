"""Helpers that work on after the run that started them went on without them (item 1351), and how their reports reach
the chat.

A helper is *detached* when the run that delegated it stopped waiting for it: the owner wrote to the run while it waited
(the wait is released, the helper is not stopped), or the run asked for no wait (``delegate`` with ``wait`` false). The
helper keeps working on its own. When it ends, its report reaches its chat the way the end of a command in the
background does (item 229): the run at work in the chat reads it at its next step as a note of Dot's, and a chat with no
run at work gets a run started to read it (:meth:`RunManager.wake_for_helpers`).

What is kept here is only which helper to tell which chat of. The report itself is the helper's own record in the store,
so a report told after the daemon restarted is the one that was told before it, from the same record. The list lives in
the store's key/value table (:data:`HELPERS_KEY`) until the report has been handed to a run, so that a restart between a
helper's end and its telling loses nothing.
"""

import secrets
from collections.abc import Collection

from pydantic import JsonValue, TypeAdapter, ValidationError

from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.store.db import Store
from dotagent.store.models import EventRecord
from dotagent.tools.context import DelegateOutcome
from dotagent.tools.delegate import render_outcome

log = get_logger(__name__)

# The live and stored event on the run that let a helper go on, so that its page says the helper works in the
# background and no longer that the run waits for it.
HELPER_RELEASED_EVENT: str = "helper_released"
# The source of the run started in a chat to tell it that helpers it was not waiting for ended (item 1351): Dot's own
# note, none of the owner's words.
HELPER_SOURCE: str = "helper_report"
# The live and stored event on the run that let a helper go on, when a restart of the daemon cut that helper off and
# the next start resumed it (:meth:`RunManager.tell_helper_reports`): its page says "cut by a restart, resuming", not
# that it failed, and names the run that goes on.
HELPER_RESUMED_EVENT: str = "helper_resumed"
# The key of the store that names the helpers whose report no run has been handed yet.
HELPERS_KEY: str = "detached_helpers"
# The most helpers one chat lets go on at once: a run that asks for no wait in a loop does not fill the Mac with them.
MOST_DETACHED_PER_CHAT: int = 8
_NONCE_BYTES: int = 8


class DetachedHelper(FrozenModel):
    """A helper run that works on after its parent stopped waiting for it, and the chat its report goes to: ``step``
    is the plan step the parent named for it, which its report does not close. ``resumed_from`` is the helper run a
    restart of the daemon cut off and this one resumes (:meth:`RunManager.tell_helper_reports`), ``None`` for a helper
    that was not cut."""

    helper_run_id: str
    parent_run_id: str
    thread_id: str
    step: int | None
    since: float
    resumed_from: str | None = None


_ADAPTER: TypeAdapter[list[DetachedHelper]] = TypeAdapter(list[DetachedHelper])


def kept_from(raw: str | None) -> list[DetachedHelper]:
    """The helpers :data:`HELPERS_KEY` holds. A value that does not read as that is logged and taken as none: what a
    restart cannot read it does not tell."""
    if raw is None:
        return []
    try:
        return _ADAPTER.validate_json(raw)
    except ValidationError as exc:
        log.error("detached_helpers_unreadable", error=str(exc))
        return []


def _with(raw: str | None, entry: DetachedHelper) -> str:
    kept: list[DetachedHelper] = [one for one in kept_from(raw) if one.helper_run_id != entry.helper_run_id]
    return _ADAPTER.dump_json([*kept, entry]).decode()


def _without(raw: str | None, helper_ids: Collection[str]) -> str:
    return _ADAPTER.dump_json([one for one in kept_from(raw) if one.helper_run_id not in helper_ids]).decode()


async def keep_detached(store: Store, entry: DetachedHelper) -> None:
    """Keep ``entry`` until its report is handed to a run."""
    await store.kv_update(HELPERS_KEY, lambda raw: _with(raw, entry))


async def forget_detached(store: Store, helper_ids: Collection[str]) -> None:
    """Forget the helpers ``helper_ids``: their report was handed to a run, or their parent's end stopped them."""
    await store.kv_update(HELPERS_KEY, lambda raw: _without(raw, helper_ids))


async def kept_detached(store: Store) -> list[DetachedHelper]:
    """Every helper whose report no run has been handed, as the store has them."""
    return kept_from(await store.kv_get(HELPERS_KEY))


def late_report_note(outcome: DelegateOutcome, entry: DetachedHelper) -> str:
    """The note of Dot's that tells a run its helper ended: the report as the delegate tool reads it back (fenced as
    third-party content when the helper read some, with its Verification note), and that it came late, so that no plan
    step was marked by it: the run checks the report and marks the step itself."""
    report: str = render_outcome(outcome, secrets.token_hex(_NONCE_BYTES), None, None).text
    step: str = (
        ""
        if entry.step is None
        else (
            f"\nIt was started for plan step {entry.step}, which stays as it is: read the report, check it, and mark "
            "the step yourself if it holds."
        )
    )
    resumed: str = (
        ""
        if entry.resumed_from is None
        else (
            f"\nA restart of Dot cut this helper off while it worked (run {entry.resumed_from}) and Dot resumed it: "
            "the report is the resumed helper's, which went on from where the first one stopped."
        )
    )
    return (
        "[system note] A helper that kept working in the background ended. Its report reached you as this note, not "
        f"as the result of a call, so no plan step was marked by it.{resumed}\n{report}{step}"
    )


def cut_report_note(outcome: DelegateOutcome, entry: DetachedHelper, why_not: str) -> str:
    """The note of Dot's that tells a run its helper was cut off by a restart of the daemon (a crash, a stop or a
    restart) and was not resumed, and why (``why_not``): the helper did not fail, its work stopped half way, so the run
    delegates it again if the work is still needed rather than taking it as a failure of the part."""
    report: str = render_outcome(outcome, secrets.token_hex(_NONCE_BYTES), None, None).text
    step: str = "" if entry.step is None else f"\nIt was started for plan step {entry.step}, which stays as it is."
    return (
        "[system note] A helper that kept working in the background did not fail: a restart of Dot cut it off before "
        f"it ended, and it was not resumed ({why_not}). Its work stopped half way: delegate it again if it is still "
        f"needed; what it did so far is in its record below.\n{report}{step}"
    )


def plan_limit_of(finished: EventRecord | None) -> tuple[bool, float | None]:
    """Whether the run whose ``run_finished`` event is ``finished`` was ended by the Claude plan's usage limit (its
    error is of the kind ``plan_limit``, item 1206), and when the plan said its window resets (epoch seconds, ``None``
    when it did not say)."""
    if finished is None:
        return False, None
    error: JsonValue = finished.data.get("error")
    if not isinstance(error, dict) or error.get("kind") != "plan_limit":
        return False, None
    context: JsonValue = error.get("context")
    resets_at: JsonValue = context.get("resets_at") if isinstance(context, dict) else None
    if isinstance(resets_at, bool) or not isinstance(resets_at, int | float):
        return True, None
    return True, float(resets_at)
