"""The owner's own messages a wait gives way to (item 1685): in Dot's own loop the run's inbox counts them until the
loop takes them, on the Claude plan the run counts them until the program says it took them in. Only the owner's own
words count, and a wait gives way to a message once."""

import asyncio

import pytest

from dotagent.agent.attachments import RunInbox, UserInput
from dotagent.claude_code.run import OwnerUnread


def _owner(text: str) -> UserInput:
    return UserInput(text=text, files=(), question_id=None, vouches=True, said=text)


def _reaction(text: str) -> UserInput:
    return UserInput(text=text, files=(), question_id=None, vouches=False, said="", reaction="quiet")


def _dots_note(text: str) -> UserInput:
    return UserInput(text=text, files=(), question_id=None, vouches=False, said="", reaction="note", of_dot=True)


def _sent_on(text: str) -> UserInput:
    """The owner's press that sends a queued message on to the run (``RunManager.tell`` with no reaction)."""
    return UserInput(text=text, files=(), question_id=None, vouches=False, said="", owner_text=text)


def test_only_the_owners_own_words_count() -> None:
    assert _owner("stop").owners_own() and _sent_on("stop").owners_own()
    answer: UserInput = UserInput(text="yes", files=(), question_id="apr_1", vouches=False, said="yes")
    assert answer.owners_own()
    assert not _reaction("👍").owners_own() and not _dots_note("the helper is done").owners_own()


def test_the_inbox_counts_the_owners_message_until_a_wait_gives_way_to_it_or_the_loop_takes_it() -> None:
    inbox: RunInbox = RunInbox()
    inbox.put_nowait(_reaction("👍"))
    inbox.put_nowait(_dots_note("a work order"))
    assert not inbox.owner_unread()
    inbox.put_nowait(_owner("stop waiting"))
    assert inbox.owner_unread()
    inbox.heed_owner()
    assert not inbox.owner_unread()
    inbox.put_nowait(_sent_on("and look at the log"))
    assert inbox.owner_unread()
    for _ in range(3):
        inbox.get_nowait()
    # The two notes and the first message are read; the queued message sent on is not, and no wait gave way to it.
    assert inbox.owner_unread()
    inbox.get_nowait()
    assert not inbox.owner_unread()


def test_the_program_holds_the_owners_message_until_it_says_it_took_it_in() -> None:
    owner: OwnerUnread = OwnerUnread()
    assert not owner.owner_unread()
    owner.sent()
    assert owner.owner_unread()
    owner.heed_owner()
    assert not owner.owner_unread()
    owner.sent()
    owner.taken()
    assert owner.owner_unread()
    owner.taken()
    assert not owner.owner_unread()


@pytest.mark.asyncio
async def test_a_wait_is_woken_by_the_owners_message_and_not_by_a_note() -> None:
    inbox: RunInbox = RunInbox()
    woken: asyncio.Task[None] = asyncio.create_task(inbox.owner_arrival())
    inbox.put_nowait(_reaction("👍"))
    await asyncio.sleep(0.05)
    assert not woken.done()
    inbox.put_nowait(_owner("stop"))
    async with asyncio.timeout(1):
        await woken
