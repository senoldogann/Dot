"""Which chat keeps its current tab when its run ends (items 696 and 1168): the owner's, whoever ran in it last, and
a run that needs something of the owner; not a helper's, nor a chat an unwatched run opened once such a run ends
needing nothing."""

from dotagent.agent.manager import keeps_current_tab
from dotagent.proactive import INITIATIVE_SOURCE
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.store.models import RunRecord

_OWNERS: str = "web"


def _run(parent_run_id: str | None, source: str) -> RunRecord:
    return RunRecord(
        id="run_one",
        thread_id="thread_one",
        goal="Check the page",
        mode="task",
        model="ollama:scripted",
        status="done",
        created_at=1.0,
        finished_at=2.0,
        result="Done.",
        steps=2,
        input_tokens=0,
        output_tokens=0,
        parent_run_id=parent_run_id,
        source=source,
        effort=None,
        speed="standard",
        once=None,
        approvals="yolo",
        goal_mode=False,
        campaign_id=None,
        agent_id=None,
        rewindable=False,
    )


def test_a_chat_an_unwatched_run_opened_closes_all_its_tabs_unless_the_run_needs_something() -> None:
    assert not keeps_current_tab(_run(None, SCHEDULE_SOURCE), True, SCHEDULE_SOURCE, "")
    assert not keeps_current_tab(_run(None, INITIATIVE_SOURCE), True, INITIATIVE_SOURCE, " ")
    assert keeps_current_tab(_run(None, SCHEDULE_SOURCE), True, SCHEDULE_SOURCE, "Your one-time code for the bank")


def test_the_owners_chat_keeps_its_tab_and_a_helpers_does_not() -> None:
    assert keeps_current_tab(_run(None, _OWNERS), False, _OWNERS, "")
    assert keeps_current_tab(_run(None, SCHEDULE_SOURCE), True, _OWNERS, "")
    assert keeps_current_tab(_run(None, _OWNERS), False, SCHEDULE_SOURCE, "")
    assert not keeps_current_tab(_run("run_parent", "delegate"), False, _OWNERS, "")
