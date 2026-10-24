"""What the router says of the end of a run and of a run that waits (item 611), as words: a stopped run's end is one
sentence and not the same twice, the answer to a message that came with a picture starts with the line that says the
picture was not read, and a wait says where to answer with a link or, when Dot has no address a phone can open, in
words."""

from dotagent.channels.intake import UNREAD_ANSWER
from dotagent.channels.router import ChannelRouter
from dotagent.store.models import EventRecord

LINK: str = "https://mac.example.ts.net/#/runs/run_1"


def _ended(status: str, result: str) -> EventRecord:
    return EventRecord(id=1, run_id="run_1", ts=1.0, kind="run_finished", data={"status": status, "result": result})


def _waits(severity: str, summary: str, question: str | None = None) -> EventRecord:
    return EventRecord(
        id=2,
        run_id="run_1",
        ts=1.0,
        kind="approval_requested",
        data={
            "approval_id": "apr_1",
            "tool": "shell",
            "summary": summary,
            "severity": severity,
            "args": {} if question is None else {"question": question},
        },
    )


def test_the_answer_of_a_run_is_what_it_finished_with() -> None:
    assert ChannelRouter._answer(_ended("done", "  It is sunny.  ")) == "It is sunny."
    assert ChannelRouter._answer(_ended("done", "")) == "Done."


def test_how_a_stopped_run_ended_is_said_once() -> None:
    """Its result is the sentence that leads the line, and the line said it twice."""
    assert ChannelRouter._answer(_ended("cancelled", "The run was stopped.")) == "The run was stopped."
    assert ChannelRouter._answer(_ended("cancelled", "")) == "The run was stopped."
    # What a stop has more to say follows the lead.
    assert ChannelRouter._answer(_ended("cancelled", "Emergency stop: every active run was stopped.")) == (
        "The run was stopped. Emergency stop: every active run was stopped."
    )
    assert ChannelRouter._answer(_ended("failed", "The run could not be started.")) == (
        "The run failed. The run could not be started."
    )
    assert ChannelRouter._answer(_ended("failed", "")) == "The run failed."
    assert ChannelRouter._answer(_ended("something else", "")) == "The run ended."


def test_the_answer_to_a_message_that_came_with_a_picture_starts_with_the_line_that_says_it_was_not_read() -> None:
    assert ChannelRouter._answer(_ended("done", "It looks fine."), unread=True) == f"{UNREAD_ANSWER}\n\nIt looks fine."
    assert ChannelRouter._answer(_ended("done", ""), unread=True) == f"{UNREAD_ANSWER}\n\nDone."
    assert ChannelRouter._answer(_ended("done", "It looks fine.")) == "It looks fine."
    # A run that gave no answer says how it ended, and nothing of the picture: there is no answer to be mistaken.
    assert ChannelRouter._answer(_ended("failed", "boom"), unread=True) == "The run failed. boom"
    assert ChannelRouter._answer(_ended("cancelled", "The run was stopped."), unread=True) == "The run was stopped."


def test_a_wait_says_what_it_asks_and_where_to_answer() -> None:
    assert ChannelRouter._asking(_waits("high", "Run `rm -r build`"), LINK) == (
        f"Dot waits for your approval: Run rm -r build\nAnswer it in Dot, not here: {LINK}"
    )
    assert ChannelRouter._asking(_waits("question", "Asked a question", "Which folder?"), LINK) == (
        f"Dot has a question: Which folder?\nAnswer it in Dot, not here: {LINK}"
    )


def test_waits_held_together_say_how_many_more_wait() -> None:
    assert ChannelRouter._asking(_waits("high", "Run `rm -r build`"), LINK, 2) == (
        f"Dot waits for your approval: Run rm -r build (and 2 more)\nAnswer them in Dot, not here: {LINK}"
    )


def test_with_no_address_a_phone_can_open_a_wait_names_no_link_and_says_where_to_answer() -> None:
    assert ChannelRouter._asking(_waits("high", "Run `rm -r build`"), None) == (
        "Dot waits for your approval: Run rm -r build\nAnswer it in Dot on your Mac, not here."
    )
    assert ChannelRouter._asking(_waits("question", "Asked a question", "Which folder?"), None, 1) == (
        "Dot has a question: Which folder? (and 1 more)\nAnswer them in Dot on your Mac, not here."
    )
