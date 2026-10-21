"""How the end of a schedule's run is told (item 461): always every end, never none, and problems a failure that
follows a run that went well (or one whose row is unknown) and an end that needs the owner, unless it was stopped."""

import pytest

from dotagent.scheduler.notices import BY_HAND_LEVEL, notify_level, tells
from dotagent.store.models import NotifyLevel, RunStatus


def test_the_level_is_the_schedules_own_else_the_configured_one_and_a_run_by_hand_is_always_told() -> None:
    assert notify_level(None, "problems") == "problems"
    assert notify_level("never", "always") == "never"
    assert BY_HAND_LEVEL == "always"


@pytest.mark.parametrize(
    ("level", "status", "needs", "failed_in_row", "told"),
    [
        ("always", "done", "", 0, True),
        ("always", "cancelled", "", 0, True),
        ("never", "failed", "Sign in.", 1, False),
        ("problems", "failed", "", 1, True),
        ("problems", "failed", "", 2, False),
        ("problems", "failed", "", None, True),
        ("problems", "done", "", 0, False),
        ("problems", "done", "Sign in to the bank.", 0, True),
        ("problems", "done", "   ", 0, False),
        ("problems", "cancelled", "Sign in to the bank.", 0, False),
    ],
)
def test_a_level_tells_the_ends_it_is_for(
    level: NotifyLevel, status: RunStatus, needs: str, failed_in_row: int | None, told: bool
) -> None:
    assert tells(level, status, needs, failed_in_row) is told
