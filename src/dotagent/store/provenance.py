"""Where a note came from (item 1864): who or what saved it, read from the run that saved it by Dot's own code.

A note's ``origin`` is set by the store as it writes the note, from the note's ``source_run`` and that run's ``source``
and ``parent_run_id``, which the manager sets as it starts the run; no tool argument reaches it, so a model cannot write
it. The runs are kept for a time and notes for longer (on the owner's Mac on 2026-11-25, the run of 1,083 of the 1,272
notes was gone), so the origin is a column of the note and not a join at read time. Every function here is pure.
"""

from dotagent.store.models import MemoryOrigin

# The run sources, as the modules that start the runs name them. They are written out here because the store sits
# below those modules; `tests/unit/test_memory_origin.py` holds each one to its constant.
# ``dotagent.scheduler.SCHEDULE_SOURCE``.
SCHEDULE_RUN: str = "schedule"
# ``dotagent.proactive.WATCHER_SOURCE``, ``REPLY_SOURCE`` and ``NOTICE_SOURCE``: runs a watched item or a reply to a
# mail started, which nobody watches.
WATCHER_RUNS: frozenset[str] = frozenset({"watcher", "reply", "notice"})
# ``dotagent.proactive.INITIATIVE_SOURCE``.
INITIATIVE_RUN: str = "initiative"
# ``dotagent.tools.delegate.DELEGATE_SOURCE`` and ``dotagent.tools.team.TEAM_SOURCE``: a run another run gave work to.
HELPER_RUNS: frozenset[str] = frozenset({"delegate", "team"})
# ``dotagent.memory.service.REFLECTION_SOURCE``: the ``source_run`` of a note the nightly reflection wrote.
REFLECTION_RUN: str = "reflection"


def run_origin(source: str, parent_run_id: str | None) -> MemoryOrigin:
    """The origin of a note a run of ``source`` saved: a run another run started (``parent_run_id``) or one of a
    delegate or a team is a helper's, whatever began its chain; then a schedule's, a watcher's (a watched item, a
    reply to a mail, a notice) and one of Dot's own initiative; any other run is one of a chat (the owner's chats, a
    ``dotx`` command, a code review, a run that resumed or told a chat of work that ended)."""
    if parent_run_id is not None or source in HELPER_RUNS:
        return "helper"
    if source == SCHEDULE_RUN:
        return "schedule"
    if source in WATCHER_RUNS:
        return "watcher"
    if source == INITIATIVE_RUN:
        return "initiative"
    return "chat"


def note_origin(source_run: str | None, run: tuple[str, str | None] | None) -> MemoryOrigin | None:
    """The origin of a note whose ``source_run`` is given and whose run, when the store holds it, has the ``source``
    and ``parent_run_id`` of ``run``: the owner's when no run saved it, the nightly reflection's, the run's
    (:func:`run_origin`), and ``None`` when the run is no longer there to say (an old note, or a run id no run has)."""
    if source_run is None:
        return "owner"
    if source_run == REFLECTION_RUN:
        return "reflection"
    if run is None:
        return None
    source, parent_run_id = run
    return run_origin(source, parent_run_id)
