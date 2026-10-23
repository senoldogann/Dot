"""The reactions on Dot's messages (item 586), in the real loop, store and run manager, on a scripted model that a test
holds at the step it wants.

While a run works, a reaction on one of its messages reaches it at its next step as a note from Dot: a "not right"
gives the step under way up, a "good" does not, a burst is one note, a hold waits for the owner (or their taking it
back, or the time of a hold), and none of them clears a run's mark of untrusted content, as a message of the owner
does. After the run ended, a "not right" starts one follow-up run in the same chat as the owner's own message, with
the message quoted for the run and not in the owner's words, queued behind a run at work, as untrusted as the run it
is about; a pin keeps the message as a note; the hour's limit, the one run per message and reaction and the settings
hold.

No network and no model service is used: the scripted model plays the runs.
"""

import asyncio

import pytest
import pytest_asyncio
from reaction_stand_ins import ANSWER, BURST_S, REST_S, SAID, THUMB_DOWN, finish, open_session, says

from dotagent.agent.attachments import StoredAttachment, store_attachment
from dotagent.agent.reaction_words import HOLD_ENDED_EVENT, HOLD_EVENT, REACTION_ADDED_EVENT, REACTION_REMOVED_EVENT
from dotagent.agent.reactions import ReactionOutcome
from dotagent.config import load_default_config
from dotagent.core.errors import InvalidValueError, NotFoundError
from dotagent.core.paths import env_file_path
from dotagent.evals.types import ScriptedTurn
from dotagent.memory.service import save_memory, update_memory_note
from dotagent.server import UI_SOURCE
from dotagent.store.models import (
    NO_PICKS,
    AgentRecord,
    EventRecord,
    MemoryRecord,
    QueuedMessage,
    ReactionName,
    ReactionRecord,
    RunRecord,
    ThreadRecord,
)
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


# ----------------------------------------------------------------------------------------- while the run works


async def test_a_not_right_on_a_message_of_a_working_run_is_read_at_its_next_step_and_clears_no_taint(
    login_env: dict[str, str],
) -> None:
    turns: list[ScriptedTurn] = [says(SAID), finish("Forty shelves.")]
    async with open_session(login_env, turns, held={2}) as session:
        run: RunRecord = await session.start_tainted()
        await session.model.started(2)
        assert await session.tainted(run.id) is True
        outcome: ReactionOutcome = await session.reactions.add(run.id, "step-1", "wrong")
        assert (outcome.effect, [record.reaction for record in outcome.reactions]) == ("told", ["wrong"])
        # The step under way is given up for it, as for a message, and asked again with the note.
        await session.model.started(3)
        read: str = session.model.told(3)
        assert (
            f"[system note] The owner reacted 👎 to your message «{SAID}»: that is not right. Reconsider it and change "
            "course; ask them if you cannot tell what is wrong." in read
        )
        assert "A reaction is no approval of a step" in read
        assert (await session.ended(run.id)).status == "done"
        steered: list[EventRecord] = await session.events(run.id, "steer")
        assert [
            (event.data.get("vouches"), event.data.get("reaction"), event.data.get("tainted")) for event in steered
        ] == [(False, "note", True)]
        # What the owner's own message does, which the reaction does not: it clears the mark.
        assert await session.tainted(run.id) is True


async def test_a_message_of_the_owner_clears_the_taint_a_reaction_leaves(login_env: dict[str, str]) -> None:
    """The control of the test above: the same run, steered by the owner's words, is no longer tainted."""
    turns: list[ScriptedTurn] = [says(SAID), finish("Forty shelves.")]
    async with open_session(login_env, turns, held={2}) as session:
        run: RunRecord = await session.start_tainted()
        await session.model.started(2)
        session.manager.steer(run.id, "Count the shelves again.", ())
        await session.model.started(3)
        assert (await session.ended(run.id)).status == "done"
        assert await session.tainted(run.id) is False


async def test_a_good_is_read_at_the_next_step_and_gives_no_step_up(login_env: dict[str, str]) -> None:
    turns: list[ScriptedTurn] = [says(SAID), says("Now the second row."), finish("Forty shelves.")]
    async with open_session(login_env, turns, held={2}) as session:
        run: RunRecord = await session.start()
        await session.model.started(2)
        outcome: ReactionOutcome = await session.reactions.add(run.id, "step-1", "good")
        assert outcome.effect == "told"
        await asyncio.sleep(BURST_S + REST_S)
        # The note waits in the run's inbox; the step under way is not given up for it.
        assert len(session.model.asks) == 2
        assert await session.events(run.id, "steer") == []
        session.model.open(2)
        assert (await session.ended(run.id)).status == "done"
        assert "[system note] The owner reacted 👍 to your message" in session.model.told(3)
        assert "keep going this way." in session.model.told(3)
        steered: list[EventRecord] = await session.events(run.id, "steer")
        assert [(event.data.get("vouches"), event.data.get("reaction")) for event in steered] == [(False, "quiet")]


async def test_a_good_that_comes_as_the_run_finishes_is_not_worth_one_more_step(login_env: dict[str, str]) -> None:
    turns: list[ScriptedTurn] = [says(SAID), finish("Forty shelves.")]
    async with open_session(login_env, turns, held={2}) as session:
        run: RunRecord = await session.start()
        await session.model.started(2)
        await session.reactions.add(run.id, "step-1", "love")
        await asyncio.sleep(BURST_S + REST_S)
        session.model.open(2)
        ended: RunRecord = await session.ended(run.id)
        assert (ended.status, ended.result) == ("done", "Forty shelves.")
        assert len(session.model.asks) == 2 and await session.events(run.id, "steer") == []


async def test_reactions_that_come_within_seconds_are_one_note(login_env: dict[str, str]) -> None:
    turns: list[ScriptedTurn] = [says(SAID), says("Now the second row."), finish("Forty shelves.")]
    async with open_session(login_env, turns, held={3}) as session:
        run: RunRecord = await session.start()
        await session.model.started(3)
        await session.reactions.add(run.id, "step-1", "wrong")
        await session.reactions.add(run.id, "step-2", "again")
        await session.reactions.add(run.id, "step-1", "explain")
        await session.model.started(4)
        read: str = session.model.told(4)
        assert read.count("[system note] The owner reacted to several of your messages:") == 1
        assert f"- 👎 on «{SAID}»:" in read
        assert "- 🔁 on «Now the second row.»:" in read
        assert f"- ❓ on «{SAID}»:" in read
        assert (await session.ended(run.id)).status == "done"
        assert len(await session.events(run.id, "steer")) == 1


async def test_a_reaction_taken_back_before_it_is_told_is_never_told(login_env: dict[str, str]) -> None:
    turns: list[ScriptedTurn] = [says(SAID), finish("Forty shelves.")]
    async with open_session(login_env, turns, held={2}) as session:
        run: RunRecord = await session.start()
        await session.model.started(2)
        await session.reactions.add(run.id, "step-1", "wrong")
        taken: ReactionOutcome = await session.reactions.remove(run.id, "step-1", "wrong")
        assert (taken.effect, taken.reactions) == ("taken_back", ())
        await asyncio.sleep(BURST_S + REST_S)
        assert len(session.model.asks) == 2 and await session.events(run.id, "steer") == []
        session.model.open(2)
        assert (await session.ended(run.id)).status == "done"
        # Both were recorded as events of the run, the page's and the audit's.
        both: list[EventRecord] = await session.runtime.store.events_of_kinds(
            run.id, [REACTION_ADDED_EVENT, REACTION_REMOVED_EVENT], 10
        )
        assert [event.kind for event in both] == [REACTION_ADDED_EVENT, REACTION_REMOVED_EVENT]
        assert both[0].data == {
            "thread_id": run.thread_id,
            "message": "step-1",
            "reaction": "wrong",
            "effect": "told",
            "follow_up_run_id": None,
        }


async def test_a_reaction_taken_back_after_it_was_told_tells_the_run_so(login_env: dict[str, str]) -> None:
    turns: list[ScriptedTurn] = [
        says(SAID),
        says("Now the second row."),
        says("Now the third row."),
        finish("Forty shelves."),
    ]
    async with open_session(login_env, turns, held={3}) as session:
        run: RunRecord = await session.start()
        await session.model.started(3)
        await session.reactions.add(run.id, "step-1", "good")
        await asyncio.sleep(BURST_S + REST_S)
        await session.reactions.remove(run.id, "step-1", "good")
        await asyncio.sleep(BURST_S + REST_S)
        session.model.open(3)
        assert (await session.ended(run.id)).status == "done"
        read: str = session.model.told(4)
        assert f"[system note] The owner reacted 👍 to your message «{SAID}»" in read
        assert f"[system note] The owner took back their 👍 on your message «{SAID}»: go on as you were." in read


async def test_a_hold_holds_the_run_before_its_next_step_until_the_owner_writes(login_env: dict[str, str]) -> None:
    turns: list[ScriptedTurn] = [says(SAID), finish("Forty shelves.")]
    async with open_session(login_env, turns, held={2}) as session:
        run: RunRecord = await session.start()
        await session.model.started(2)
        outcome: ReactionOutcome = await session.reactions.add(run.id, "step-1", "hold")
        assert outcome.effect == "held"
        await session.held(run.id)
        await asyncio.sleep(REST_S)
        # The step under way was given up, and no other was asked: the run waits.
        assert len(session.model.asks) == 2
        assert [event.data["step"] for event in await session.events(run.id, HOLD_EVENT)] == [1]
        assert (await session.runtime.store.get_run(run.id)).status == "running"
        session.manager.steer(run.id, "Use the other folder.", ())
        await session.model.started(3)
        assert not session.manager.holding(run.id)
        read: str = session.model.told(3)
        assert "[system note] The owner reacted ✋ to your message" in read and "Use the other folder." in read
        assert (await session.ended(run.id)).status == "done"
        ended: list[EventRecord] = await session.events(run.id, HOLD_ENDED_EVENT)
        assert [event.data["reason"] for event in ended] == ["message"]


async def test_a_hold_taken_back_lets_the_run_go_on(login_env: dict[str, str]) -> None:
    turns: list[ScriptedTurn] = [says(SAID), finish("Forty shelves.")]
    async with open_session(login_env, turns, held={2}) as session:
        run: RunRecord = await session.start()
        await session.model.started(2)
        await session.reactions.add(run.id, "step-1", "hold")
        await session.held(run.id)
        taken: ReactionOutcome = await session.reactions.remove(run.id, "step-1", "hold")
        assert taken.effect == "taken_back"
        await session.model.started(3)
        assert "took back their ✋" in session.model.told(3)
        assert (await session.ended(run.id)).status == "done"
        assert [event.data["reason"] for event in await session.events(run.id, HOLD_ENDED_EVENT)] == ["released"]


async def test_a_message_queued_while_the_run_is_held_lets_it_go_on_and_waits_for_its_end(
    login_env: dict[str, str],
) -> None:
    turns: list[ScriptedTurn] = [says(SAID), finish("Forty shelves."), finish("Thanks too.")]
    async with open_session(login_env, turns, held={2}) as session:
        run: RunRecord = await session.start()
        await session.model.started(2)
        await session.reactions.add(run.id, "step-1", "hold")
        await session.held(run.id)
        queued: QueuedMessage = await session.manager.queue(
            run.thread_id, "Thanks.", (), "task", None, None, "standard", None, NO_PICKS, "yolo", False, None, UI_SOURCE
        )
        assert queued.text == "Thanks."
        await session.model.started(3)
        assert "The owner wrote in this chat; their message waits in its queue" in session.model.told(3)
        assert (await session.ended(run.id)).status == "done"
        follow: RunRecord = await session.next_run(run.thread_id, {run.id})
        assert follow.goal == "Thanks."
        await session.ended(follow.id)


async def test_a_run_held_and_forgotten_stops_after_the_time_of_a_hold(login_env: dict[str, str]) -> None:
    turns: list[ScriptedTurn] = [says(SAID), finish("Forty shelves.")]
    async with open_session(login_env, turns, held={2}, hold_minutes=0.02) as session:
        run: RunRecord = await session.start()
        await session.model.started(2)
        await session.reactions.add(run.id, "step-1", "hold")
        ended: RunRecord = await session.ended(run.id)
        assert ended.status == "cancelled"
        assert ended.result.startswith("Held at your word, and nobody wrote to it for 0.02 minutes")
        assert [event.data["reason"] for event in await session.events(run.id, HOLD_ENDED_EVENT)] == ["lapsed"]


async def test_a_run_held_is_stopped_by_the_owners_stop(login_env: dict[str, str]) -> None:
    turns: list[ScriptedTurn] = [says(SAID), finish("Forty shelves.")]
    async with open_session(login_env, turns, held={2}) as session:
        run: RunRecord = await session.start()
        await session.model.started(2)
        await session.reactions.add(run.id, "step-1", "hold")
        await session.held(run.id)
        await session.manager.stop(run.id)
        ended: RunRecord = await session.ended(run.id)
        assert (ended.status, ended.result) == ("cancelled", "The run was stopped.")


async def test_a_reaction_that_comes_as_the_run_ends_is_done_as_after_the_run(login_env: dict[str, str]) -> None:
    """The run ends before the note reaches it: a "not right" on what it said is a follow-up run, as after the run, and
    the pages hear what the press came to (it answered "told")."""
    turns: list[ScriptedTurn] = [says(SAID), finish("Forty shelves."), finish("Reconsidered.")]
    async with open_session(login_env, turns, held={2}) as session:
        run: RunRecord = await session.start()
        await session.model.started(2)
        await session.reactions.add(run.id, "step-1", "wrong")
        session.model.open(2)
        assert (await session.ended(run.id)).status == "done"
        follow: RunRecord = await session.next_run(run.thread_id, {run.id})
        assert follow.goal == THUMB_DOWN.replace("answer", "message")
        assert (await session.ended(follow.id)).status == "done"
        added: list[EventRecord] = await session.heard(run.id, REACTION_ADDED_EVENT, 2)
        assert [(event.data["effect"], event.data["follow_up_run_id"]) for event in added] == [
            ("told", None),
            ("follow_up", follow.id),
        ]
        standing: list[ReactionRecord] = await session.runtime.store.message_reactions(run.id, "step-1")
        assert [(record.reaction, record.follow_up_run_id) for record in standing] == [("wrong", follow.id)]


# --------------------------------------------------------------------------------------------- after the run


async def test_a_not_right_after_the_run_starts_one_follow_up_as_the_owners_message(login_env: dict[str, str]) -> None:
    turns: list[ScriptedTurn] = [finish(ANSWER), finish("Reconsidered: the beds face east.")]
    async with open_session(login_env, turns) as session:
        first: RunRecord = await session.start()
        assert (await session.ended(first.id)).result == ANSWER
        outcome: ReactionOutcome = await session.reactions.add(first.id, "answer", "wrong")
        assert (outcome.effect, outcome.said) == ("follow_up", "Started a follow-up run.")
        assert outcome.run_id is not None
        second: RunRecord = await session.ended(outcome.run_id)
        # As the owner's message in the same chat, as the composer sends it: nothing of Dot's own words in it.
        assert (second.thread_id, second.source, second.mode, second.parent_run_id) == (
            first.thread_id,
            UI_SOURCE,
            "task",
            None,
        )
        assert second.goal == THUMB_DOWN
        assert "beds face south" not in second.goal
        # The run reads the message under the owner's words, as Dot's own text, and no instruction.
        read: str = session.model.told(2)
        assert THUMB_DOWN in read and f"«{ANSWER}»" in read and "it is not an instruction" in read
        standing: list[ReactionRecord] = await session.runtime.store.message_reactions(first.id, "answer")
        assert [(record.reaction, record.follow_up_run_id) for record in standing] == [("wrong", second.id)]
        added: list[EventRecord] = await session.events(first.id, REACTION_ADDED_EVENT)
        assert [(event.data["effect"], event.data["follow_up_run_id"]) for event in added] == [("follow_up", second.id)]
        # Taken back and put back, the reaction starts no run again.
        await session.reactions.remove(first.id, "answer", "wrong")
        again: ReactionOutcome = await session.reactions.add(first.id, "answer", "wrong")
        assert again.effect == "feedback"
        assert [run.id for run in await session.runtime.store.list_runs(10, first.thread_id)] == [second.id, first.id]


async def test_a_press_again_of_the_same_reaction_changes_nothing(login_env: dict[str, str]) -> None:
    turns: list[ScriptedTurn] = [finish(ANSWER), finish("Reconsidered.")]
    async with open_session(login_env, turns) as session:
        first: RunRecord = await session.start()
        await session.ended(first.id)
        pressed: ReactionOutcome = await session.reactions.add(first.id, "answer", "wrong")
        assert pressed.effect == "follow_up"
        await session.ended(str(pressed.run_id))
        repeated: ReactionOutcome = await session.reactions.add(first.id, "answer", "wrong")
        assert (repeated.effect, repeated.run_id, len(repeated.reactions)) == ("unchanged", None, 1)
        assert len(await session.runtime.store.list_runs(10, first.thread_id)) == 2
        nothing: ReactionOutcome = await session.reactions.remove(first.id, "answer", "love")
        assert (nothing.effect, nothing.reactions[0].reaction) == ("unchanged", "wrong")


async def test_a_follow_up_takes_the_chats_taint_as_any_follow_up_does(login_env: dict[str, str]) -> None:
    """A message typed in the composer starts a run unmarked; a reaction is too small a word to vouch for what a run
    read."""
    turns: list[ScriptedTurn] = [finish(ANSWER), finish("Reconsidered."), finish(ANSWER), finish("Reconsidered.")]
    async with open_session(login_env, turns) as session:
        clean: RunRecord = await session.start()
        await session.ended(clean.id)
        fresh: ReactionOutcome = await session.reactions.add(clean.id, "answer", "wrong")
        assert await session.tainted(str(fresh.run_id)) is False
        await session.ended(str(fresh.run_id))
        read: RunRecord = await session.start_tainted()
        assert (await session.ended(read.id)).result == ANSWER
        tainted: ReactionOutcome = await session.reactions.add(read.id, "answer", "wrong")
        assert tainted.effect == "follow_up" and tainted.run_id is not None
        queued: EventRecord | None = await session.runtime.store.last_event(tainted.run_id, "run_queued")
        assert queued is not None and queued.data["tainted"] is True
        assert await session.tainted(tainted.run_id) is True
        await session.ended(tainted.run_id)


async def test_a_follow_up_waits_in_the_queue_behind_a_run_at_work_and_starts_when_it_ends(
    login_env: dict[str, str],
) -> None:
    turns: list[ScriptedTurn] = [finish(ANSWER), says("Working on it."), finish("Done."), finish("Reconsidered.")]
    async with open_session(login_env, turns, held={3}) as session:
        first: RunRecord = await session.start()
        await session.ended(first.id)
        working: RunRecord = await session.start(first.thread_id)
        await session.model.started(3)
        outcome: ReactionOutcome = await session.reactions.add(first.id, "answer", "wrong")
        assert (outcome.effect, outcome.run_id) == ("queued", None)
        waiting: list[QueuedMessage] = await session.runtime.store.list_queued(first.thread_id)
        assert [(message.text, message.source) for message in waiting] == [(THUMB_DOWN, UI_SOURCE)]
        assert waiting[0].follow_up is not None and waiting[0].follow_up.run_id == first.id
        session.model.open(3)
        assert (await session.ended(working.id)).status == "done"
        follow: RunRecord = await session.next_run(first.thread_id, {first.id, working.id})
        assert (await session.ended(follow.id)).goal == THUMB_DOWN
        # The queue handed the context over with the owner's words, and the reaction knows its run.
        assert f"«{ANSWER}»" in session.model.told(4)
        standing: list[ReactionRecord] = await session.runtime.store.message_reactions(first.id, "answer")
        assert [record.follow_up_run_id for record in standing] == [follow.id]
        assert await session.runtime.store.list_queued(first.thread_id) == []


async def test_a_queued_follow_up_edited_with_a_file_starts_with_that_file(login_env: dict[str, str]) -> None:
    turns: list[ScriptedTurn] = [finish(ANSWER), says("Working on it."), finish("Done."), finish("Reconsidered.")]
    async with open_session(login_env, turns, held={3}) as session:
        first: RunRecord = await session.start()
        await session.ended(first.id)
        working: RunRecord = await session.start(first.thread_id)
        await session.model.started(3)
        await session.reactions.add(first.id, "answer", "wrong")
        [queued] = await session.runtime.store.list_queued(first.thread_id)
        # The owner puts a file on what waits in the queue, as on any message.
        notes: StoredAttachment = store_attachment(session.runtime.workspace, "notes.txt", "text/plain", b"two lines\n")
        await session.manager.edit_queued_with_files(queued.id, queued.text, (notes,))
        session.model.open(3)
        assert (await session.ended(working.id)).status == "done"
        follow: RunRecord = await session.next_run(first.thread_id, {first.id, working.id})
        assert (await session.ended(follow.id)).goal == THUMB_DOWN
        # The file goes with the owner's words and the quote of the message, and the page is told of it as for any run.
        read: str = session.model.told(4)
        assert THUMB_DOWN in read and f"«{ANSWER}»" in read and "two lines" in read
        attached: list[EventRecord] = await session.events(follow.id, "attachments")
        assert [[file["name"] for file in event.data["files"]] for event in attached] == [["notes.txt"]]  # type: ignore[union-attr, index]
        assert await session.runtime.store.list_queued(first.thread_id) == []


async def test_a_queued_follow_up_in_an_agents_chat_starts_as_the_agents_run_with_the_file_put_on_it(
    login_env: dict[str, str],
) -> None:
    turns: list[ScriptedTurn] = [finish(ANSWER), finish("Done."), finish("Reconsidered.")]
    async with open_session(login_env, turns, held={2}) as session:
        agent: AgentRecord = await session.agent()
        first: RunRecord = await session.start_agent(agent)
        await session.ended(first.id)
        working: RunRecord = await session.start_agent(agent, first.thread_id)
        await session.model.started(2)
        await session.reactions.add(first.id, "answer", "wrong")
        [queued] = await session.runtime.store.list_queued(first.thread_id)
        assert queued.agent_id == agent.id
        notes: StoredAttachment = store_attachment(session.runtime.workspace, "notes.txt", "text/plain", b"two lines\n")
        await session.manager.edit_queued_with_files(queued.id, queued.text, (notes,))
        session.model.open(2)
        assert (await session.ended(working.id)).status == "done"
        follow: RunRecord = await session.next_run(first.thread_id, {first.id, working.id})
        ended: RunRecord = await session.ended(follow.id)
        # It starts as the agent's own run, within the agent's bounds, as a message of the owner for it would.
        assert (ended.agent_id, ended.goal, ended.status) == (agent.id, THUMB_DOWN, "done")
        read: str = session.model.told(3)
        assert THUMB_DOWN in read and f"«{ANSWER}»" in read and "two lines" in read
        attached: list[EventRecord] = await session.events(follow.id, "attachments")
        assert [[file["name"] for file in event.data["files"]] for event in attached] == [["notes.txt"]]  # type: ignore[union-attr, index]


async def test_a_queued_reaction_sent_to_the_run_at_work_vouches_for_nothing_and_is_the_owners_message_to_draw(
    login_env: dict[str, str],
) -> None:
    turns: list[ScriptedTurn] = [finish(ANSWER), says("Working on it."), finish("Done.")]
    async with open_session(login_env, turns, held={3}) as session:
        first: RunRecord = await session.start()
        await session.ended(first.id)
        working: RunRecord = await session.start_tainted(first.thread_id)
        await session.model.started(3)
        await session.reactions.add(first.id, "answer", "wrong")
        [queued] = await session.runtime.store.list_queued(first.thread_id)
        # The owner may put a file on what waits in the queue; it goes on with the words.
        notes: StoredAttachment = store_attachment(session.runtime.workspace, "notes.txt", "text/plain", b"two lines\n")
        await session.manager.edit_queued_with_files(queued.id, queued.text, (notes,))
        await session.manager.steer_queued(queued.id)
        await session.model.started(4)
        read: str = session.model.told(4)
        assert "The owner sent their reaction on from the queue" in read and f"«{ANSWER}»" in read
        assert "two lines" in read
        steered: list[EventRecord] = await session.events(working.id, "steer")
        # The page draws the owner's own words, as theirs, and no reaction is named: a press of theirs on the queue. The
        # note around the words vouches for nothing all the same.
        assert [
            (event.data.get("vouches"), event.data.get("reaction"), event.data.get("owner_text")) for event in steered
        ] == [(False, None, THUMB_DOWN)]
        assert [file["name"] for file in steered[0].data["files"]] == ["notes.txt"]  # type: ignore[union-attr, index]
        assert (await session.ended(working.id)).status == "done"
        assert await session.tainted(working.id) is True
        assert await session.runtime.store.list_queued(first.thread_id) == []


async def test_a_queued_reaction_sent_on_while_the_run_is_held_lets_it_go_on_as_a_message_does(
    login_env: dict[str, str],
) -> None:
    turns: list[ScriptedTurn] = [finish(ANSWER), says(SAID), finish("Done.")]
    async with open_session(login_env, turns, held={3}) as session:
        first: RunRecord = await session.start()
        await session.ended(first.id)
        working: RunRecord = await session.start(first.thread_id)
        await session.model.started(3)
        await session.reactions.add(working.id, "step-1", "hold")
        await session.held(working.id)
        await session.reactions.add(first.id, "answer", "wrong")
        [queued] = await session.runtime.store.list_queued(first.thread_id)
        await asyncio.sleep(REST_S)
        assert session.manager.holding(working.id)
        await session.manager.steer_queued(queued.id)
        assert (await session.ended(working.id)).status == "done"
        assert not session.manager.holding(working.id)
        # The owner's press wrote to the run: the hold ended by a message, not by their taking the ✋ back.
        ended: list[EventRecord] = await session.events(working.id, HOLD_ENDED_EVENT)
        assert [event.data["reason"] for event in ended] == ["message"]
        steered: list[EventRecord] = await session.events(working.id, "steer")
        assert [(event.data.get("reaction"), event.data.get("owner_text")) for event in steered] == [
            ("hold", None),
            (None, THUMB_DOWN),
        ]


async def test_what_a_delegated_run_or_a_campaigns_session_said_is_kept_as_feedback_and_starts_no_run(
    login_env: dict[str, str],
) -> None:
    async with open_session(login_env, [finish(ANSWER)]) as session:
        store = session.runtime.store
        parent: RunRecord = await session.start()
        await session.ended(parent.id)
        thread: ThreadRecord = await store.create_thread("Elsewhere", "ui")
        for parent_id, campaign_id in ((parent.id, None), (None, "cmp_shelves")):
            run: RunRecord = await store.create_run(
                thread.id,
                "Look",
                "task",
                "ollama:x",
                parent_id,
                "ui",
                "medium",
                "standard",
                None,
                "yolo",
                False,
                campaign_id,
                None,
                None,
            )
            await store.end_run(run.id, "done", "Found it.", {"status": "done", "result": "Found it."})
            outcome: ReactionOutcome = await session.reactions.add(run.id, "answer", "wrong")
            assert (outcome.effect, outcome.run_id) == ("feedback", None)
        assert len(await store.list_runs(10, thread.id)) == 2


async def test_a_good_is_only_feedback_and_a_setting_makes_it_a_follow_up(login_env: dict[str, str]) -> None:
    turns: list[ScriptedTurn] = [finish(ANSWER), finish("Glad of it.")]
    async with open_session(login_env, turns) as session:
        first: RunRecord = await session.start()
        await session.ended(first.id)
        kept: ReactionOutcome = await session.reactions.add(first.id, "answer", "good")
        assert (kept.effect, kept.run_id) == ("feedback", None)
        assert len(await session.runtime.store.list_runs(10, first.thread_id)) == 1
        session.reactions.apply("good", "follow_up")
        await session.reactions.remove(first.id, "answer", "good")
        started: ReactionOutcome = await session.reactions.add(first.id, "answer", "good")
        assert started.effect == "follow_up" and started.run_id is not None
        follow: RunRecord = await session.ended(started.run_id)
        assert follow.goal == "My reaction 👍 to your answer above: good, that is what I wanted. Carry on from there."


async def test_the_switch_of_the_follow_ups_keeps_every_reaction_as_feedback_and_a_pin_still_makes_its_note(
    login_env: dict[str, str],
) -> None:
    turns: list[ScriptedTurn] = [finish(ANSWER), finish("Reconsidered.")]
    names: tuple[ReactionName, ...] = ("wrong", "again", "explain")
    async with open_session(login_env, turns, follow_ups=False) as session:
        first: RunRecord = await session.start()
        await session.ended(first.id)
        for name in names:
            outcome: ReactionOutcome = await session.reactions.add(first.id, "answer", name)
            assert (outcome.effect, outcome.run_id) == ("feedback", None)
        pinned: ReactionOutcome = await session.reactions.add(first.id, "answer", "remember")
        assert pinned.effect == "noted"
        assert len(await session.runtime.store.list_runs(10, first.thread_id)) == 1
        # Switched on again, a reaction put back is still its own one run, which a setting can take away.
        session.reactions.apply("follow_ups", True)
        session.reactions.apply("wrong", "feedback")
        await session.reactions.remove(first.id, "answer", "again")
        started: ReactionOutcome = await session.reactions.add(first.id, "answer", "again")
        assert started.effect == "follow_up"
        await session.ended(str(started.run_id))


async def test_at_most_the_hours_limit_of_follow_ups_start_and_the_rest_are_kept_as_feedback(
    login_env: dict[str, str],
) -> None:
    turns: list[ScriptedTurn] = [finish(ANSWER)] * 3 + [finish("Reconsidered.")] * 2
    async with open_session(login_env, turns, follow_ups_per_hour=2) as session:
        runs: list[RunRecord] = []
        for _ in range(3):
            run: RunRecord = await session.start()
            await session.ended(run.id)
            runs.append(run)
        outcomes: list[ReactionOutcome] = []
        for run in runs:
            outcome: ReactionOutcome = await session.reactions.add(run.id, "answer", "wrong")
            outcomes.append(outcome)
            if outcome.run_id is not None:
                await session.ended(outcome.run_id)
        assert [outcome.effect for outcome in outcomes] == ["follow_up", "follow_up", "limit"]
        assert "limit ([reactions] follow_ups_per_hour)" in outcomes[2].said
        limited: list[ReactionRecord] = await session.runtime.store.message_reactions(runs[2].id, "answer")
        assert [record.reaction for record in limited] == ["wrong"]


# ------------------------------------------------------------------------------------------------- the pin


async def test_a_pin_keeps_the_message_as_a_note_and_taking_it_back_forgets_the_note(login_env: dict[str, str]) -> None:
    turns: list[ScriptedTurn] = [finish("Use pnpm, not npm, in this repo.")]
    async with open_session(login_env, turns) as session:
        run: RunRecord = await session.start()
        await session.ended(run.id)
        pinned: ReactionOutcome = await session.reactions.add(run.id, "answer", "remember")
        assert (pinned.effect, pinned.said) == ("noted", "Kept as a note.")
        [note] = await session.runtime.memory.store.list_memories(10, 0)
        assert (note.content, note.tags, note.tainted, note.source_run) == (
            "Use pnpm, not npm, in this repo.",
            ("from-chat",),
            False,
            None,
        )
        assert [record.note_id for record in pinned.reactions] == [note.id]
        gone: ReactionOutcome = await session.reactions.remove(run.id, "answer", "remember")
        assert (gone.effect, gone.said) == ("note_gone", "The note is gone from memory.")
        assert await session.runtime.memory.store.list_memories(10, 0) == []
        # No run was started for a pin.
        assert len(await session.runtime.store.list_runs(10, run.thread_id)) == 1


async def test_a_note_a_pin_makes_is_marked_as_read_from_outside_content_when_the_run_read_some(
    login_env: dict[str, str],
) -> None:
    turns: list[ScriptedTurn] = [finish("The invoice is due on the 3rd.")]
    async with open_session(login_env, turns) as session:
        run: RunRecord = await session.start_tainted()
        await session.ended(run.id)
        pinned: ReactionOutcome = await session.reactions.add(run.id, "answer", "remember")
        assert pinned.said == "Kept as a note. It is marked as read from outside content."
        [note] = await session.runtime.memory.store.list_memories(10, 0)
        assert note.tainted is True


async def test_a_pin_on_a_message_of_a_run_at_work_keeps_a_note_and_tells_the_run_nothing(
    login_env: dict[str, str],
) -> None:
    turns: list[ScriptedTurn] = [says("The shelves are numbered from the door."), finish("Forty shelves.")]
    async with open_session(login_env, turns, held={2}) as session:
        run: RunRecord = await session.start()
        await session.model.started(2)
        pinned: ReactionOutcome = await session.reactions.add(run.id, "step-1", "remember")
        assert pinned.effect == "noted"
        await asyncio.sleep(BURST_S + REST_S)
        [note] = await session.runtime.memory.store.list_memories(10, 0)
        assert note.content == "The shelves are numbered from the door."
        assert len(session.model.asks) == 2 and await session.events(run.id, "steer") == []
        session.model.open(2)
        await session.ended(run.id)


async def test_a_note_that_was_there_or_was_changed_since_stays_when_the_pin_is_taken_back(
    login_env: dict[str, str],
) -> None:
    turns: list[ScriptedTurn] = [finish("Use pnpm, not npm, in this repo."), finish("Prices are in euros.")]
    async with open_session(login_env, turns) as session:
        memory = session.runtime.memory
        first: RunRecord = await session.start()
        await session.ended(first.id)
        await save_memory(memory, "Use pnpm, not npm, in this repo.", (), None, None, None, False)
        await session.reactions.add(first.id, "answer", "remember")
        kept: ReactionOutcome = await session.reactions.remove(first.id, "answer", "remember")
        assert kept.effect == "note_kept"
        assert [note.content for note in await memory.store.list_memories(10, 0)] == [
            "Use pnpm, not npm, in this repo."
        ]
        second: RunRecord = await session.start(first.thread_id)
        await session.ended(second.id)
        await session.reactions.add(second.id, "answer", "remember")
        notes: list[MemoryRecord] = [
            note for note in await memory.store.list_memories(10, 0) if note.content == "Prices are in euros."
        ]
        await update_memory_note(memory, notes[0].id, "Prices are in euros, with VAT.", ("from-chat",))
        changed: ReactionOutcome = await session.reactions.remove(second.id, "answer", "remember")
        assert (changed.effect, changed.said) == ("note_kept", "The note stays in memory: it was changed since.")
        assert len(await memory.store.list_memories(10, 0)) == 2


async def test_a_long_message_is_kept_as_a_note_cut_to_the_size_of_a_note_and_a_message_too_short_is_refused(
    login_env: dict[str, str],
) -> None:
    long: str = " ".join(f"word{number}" for number in range(300))
    turns: list[ScriptedTurn] = [finish(long), finish("ok")]
    async with open_session(login_env, turns) as session:
        first: RunRecord = await session.start()
        await session.ended(first.id)
        await session.reactions.add(first.id, "answer", "remember")
        [note] = await session.runtime.memory.store.list_memories(10, 0)
        limit: int = session.runtime.config.memory.max_chars
        assert len(note.content) <= limit and note.content.endswith("…") and note.content.startswith("word0 word1")
        second: RunRecord = await session.start(first.thread_id)
        await session.ended(second.id)
        with pytest.raises(InvalidValueError):
            await session.reactions.add(second.id, "answer", "remember")
        # Nothing was left standing for the pin that could not be kept.
        assert await session.runtime.store.message_reactions(second.id, "answer") == []


async def test_a_pin_on_a_message_that_holds_a_secret_is_refused_as_the_memory_tools_refuse_such_a_note(
    login_env: dict[str, str],
) -> None:
    held: str = "plain-held-value-4821"
    key: str = "sk-" + "abcdefghijklmnop" * 2
    turns: list[ScriptedTurn] = [finish("The depot closes at six."), finish("The depot opens at eight.")]
    async with open_session(login_env, turns) as session:
        # A secret Dot holds that no pattern knows, and a string that looks like a key: the stored text of a run is
        # masked where it can be, so these are put in as a store from before would hold them.
        env_file_path().write_text(f"STAGING_PASSWORD={held}\n", encoding="utf-8")
        for stored in (f"The staging password is {held}, keep it close.", f"Use {key} for the depot's service."):
            run: RunRecord = await session.start()
            await session.ended(run.id)
            await session.runtime.store.finish_run(run.id, "done", stored)
            with pytest.raises(InvalidValueError) as refused:
                await session.reactions.add(run.id, "answer", "remember")
            # The refusal says what is wrong, not what the secret is, and leaves neither a note nor the pin standing.
            assert "holds a secret" in refused.value.message
            assert held not in repr(refused.value.context) and key not in repr(refused.value.context)
            assert await session.runtime.store.message_reactions(run.id, "answer") == []
        assert await session.runtime.memory.store.list_memories(10, 0) == []


async def test_a_reaction_on_what_a_run_has_not_said_is_refused(login_env: dict[str, str]) -> None:
    turns: list[ScriptedTurn] = [says(SAID), finish("Forty shelves.")]
    async with open_session(login_env, turns, held={2}) as session:
        run: RunRecord = await session.start()
        await session.model.started(2)
        with pytest.raises(InvalidValueError):
            await session.reactions.add(run.id, "answer", "good")
        with pytest.raises(NotFoundError):
            await session.reactions.add(run.id, "step-9", "good")
        session.model.open(2)
        await session.ended(run.id)
        assert await session.runtime.store.thread_reactions(run.thread_id) == []
