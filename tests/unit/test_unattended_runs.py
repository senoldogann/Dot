"""Item 2016: which runs nobody started, whose processes keep the sandbox in every mode. A schedule's, a watcher's
(a hook's among both), Dot's own initiative's, a reply's, a notice's and a reflection's run are; a run that goes on in a
chat without the owner's message is as the chat's opener; the owner's message, a phone's, `dotx run`, a review and an
eval's task are the owner's; an origin that cannot be read is unattended."""

import pytest

from dotagent.agent.helper_reports import HELPER_SOURCE
from dotagent.agent.manager import BACKGROUND_SOURCE, WORK_ORDER_SOURCE, unattended_origin
from dotagent.agent.welcome import WELCOME_SOURCE
from dotagent.channels.types import TITLES
from dotagent.evals import EVAL_SOURCE
from dotagent.memory.service import REFLECTION_SOURCE
from dotagent.proactive import INITIATIVE_SOURCE, NOTICE_SOURCE, REPLY_SOURCE, WATCHER_SOURCE
from dotagent.scheduler import SCHEDULE_SOURCE
from dotagent.server import CLI_SOURCE, REVIEW_SOURCE, UI_SOURCE
from dotagent.tools.delegate import DELEGATE_SOURCE

NOBODY: tuple[str, ...] = (
    SCHEDULE_SOURCE,
    WATCHER_SOURCE,
    INITIATIVE_SOURCE,
    REPLY_SOURCE,
    NOTICE_SOURCE,
    REFLECTION_SOURCE,
)
CARRIED: tuple[str, ...] = (BACKGROUND_SOURCE, WORK_ORDER_SOURCE, HELPER_SOURCE, WELCOME_SOURCE)
# The app and a phone start a run as `ui`; iMessage with its channel's id (``dotagent.channels.types.TITLES``).
OWNERS: tuple[str, ...] = (UI_SOURCE, CLI_SOURCE, REVIEW_SOURCE, EVAL_SOURCE, *TITLES)


@pytest.mark.parametrize("origin", NOBODY)
def test_a_run_nobody_started_is_unattended_in_any_chat(origin: str) -> None:
    assert unattended_origin(origin, UI_SOURCE)
    assert unattended_origin(origin, origin)


@pytest.mark.parametrize("origin", OWNERS)
def test_a_run_the_owner_started_is_theirs_in_any_chat(origin: str) -> None:
    assert not unattended_origin(origin, UI_SOURCE)
    assert not unattended_origin(origin, SCHEDULE_SOURCE)
    assert not unattended_origin(origin, INITIATIVE_SOURCE)


@pytest.mark.parametrize("origin", CARRIED)
def test_a_run_that_goes_on_without_the_owners_message_is_as_the_chats_opener(origin: str) -> None:
    for opener in NOBODY:
        assert unattended_origin(origin, opener), opener
    assert not unattended_origin(origin, UI_SOURCE)
    assert not unattended_origin(origin, None)


def test_an_origin_that_cannot_be_read_is_unattended() -> None:
    assert unattended_origin(None, UI_SOURCE)


def test_a_delegated_runs_own_source_does_not_decide_it() -> None:
    """A helper is as the run that delegated it (the manager reads the parent's gate), never by its own source."""
    assert not unattended_origin(DELEGATE_SOURCE, SCHEDULE_SOURCE)
