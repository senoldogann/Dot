"""A run whose own history was rewritten out of what tools returned (item 1219): the steps after a compaction are
checked as the steps after untrusted content are, and what the run saves since is still its own."""

from dotagent.agent.continuity import new_ledger
from dotagent.agent.gate import RunGate, tainted_after
from dotagent.agent.loop import Compaction, _compaction_event
from dotagent.store.models import TAINT_EVENTS, EventRecord


def _gate() -> RunGate:
    gate: RunGate = object.__new__(RunGate)
    gate.tainted = False
    gate.ever_tainted = False
    gate._noted_yes = frozenset({"a note the owner gave"})
    return gate


def test_a_compaction_puts_the_steps_after_it_under_the_check() -> None:
    gate: RunGate = _gate()
    gate.rewrite_from_outside()
    assert gate.tainted is True


def test_a_compaction_leaves_what_the_run_saves_since_its_own() -> None:
    """The note is the run's story, not a page it read: memory and todos it writes after one are not marked as learned
    from third-party content."""
    gate: RunGate = _gate()
    gate.rewrite_from_outside()
    assert gate.ever_tainted is False


def test_a_compaction_lets_a_note_the_owner_gave_go() -> None:
    """The note said yes to an action as the history described it; the summary may say it otherwise, so it is asked
    again (item 1140)."""
    gate: RunGate = _gate()
    gate.rewrite_from_outside()
    assert gate._noted_yes == frozenset()


def test_the_compaction_event_carries_the_mark_so_a_resumed_run_starts_untrusted() -> None:
    """A run cut off right after a compaction is resumed from its events: the mark has to be in the event, not only in
    the gate that made it (item 1219)."""
    ledger = new_ledger("Do the thing.")
    data: dict[str, object] = _compaction_event(
        Compaction.model_construct(
            note="x", method="model", model="ollama:test", attempts=1, problem=None, fast_problem=None, check=None
        ),
        3,
        ledger,
        100,
        40,
    )
    assert data["tainted"] is True
    assert "compacted" in TAINT_EVENTS
    event: EventRecord = EventRecord(id=1, run_id="run-1", ts=1.0, kind="compacted", data=data)
    assert tainted_after([event]) is True
