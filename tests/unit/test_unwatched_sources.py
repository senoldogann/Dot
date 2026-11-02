"""Every rule of a run nobody watches counts each source of :data:`dotagent.proactive.UNWATCHED_SOURCES` (the fourth
pre-push review of item 926: the source of a reply's run was one of four lists, so its run could wait on a question
nobody would answer, take the owner's window and react to words they never wrote)."""

from dotagent.agent import loop, manager, progress
from dotagent.proactive import REPLY_SOURCE, UNWATCHED_SOURCES


def test_every_rule_of_a_run_nobody_watches_counts_a_replys_run() -> None:
    assert REPLY_SOURCE in UNWATCHED_SOURCES
    for rule in (progress._UNATTENDED_SOURCES, manager._NOBODY_WATCHES, loop._UNASKED_SOURCES):
        assert UNWATCHED_SOURCES.issubset(rule)
    # So such a run asks nobody a question that waits, in any mode.
    assert progress.can_ask_user(REPLY_SOURCE, None, "ask") is False
