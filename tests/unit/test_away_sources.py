"""What "while you were away" lists (item 1424), on a real store with a fixture run of each source: what Dot starts by
itself and nobody waits for (a schedule's, a watcher's, Dot's own initiative, a reply's, an agent's work, a command that
ended in the background), and what ran in an agent's own chat under a source that is not the owner's; never a run the
owner started and waited for (``ui``, ``cli``, a review, an eval, an agent's welcome, a resumed run), a helper's, one
that ended before the moment, or a source of Dot's own that no rule names in a chat that is no agent's. The names, the
counts, the decisions and the drafts follow the same runs, and the moment is the one the owner marked (item 1356)."""

import asyncio
import time
from pathlib import Path

from dotagent.scheduler.plans import UNSET_OPTIONS
from dotagent.server.away import AWAY_RULE, mark_read, read_away, read_mark
from dotagent.server.views import AwayView
from dotagent.store.db import Store
from dotagent.store.models import RunRecord, ThreadRecord


async def _run(store: Store, thread: ThreadRecord, source: str, parent: str | None = None) -> RunRecord:
    return await store.create_run(
        thread.id, f"goal of {source}", "task", "m:m", parent, source, "medium", "standard", None, "yolo", False, None,
        thread.agent_id, None,
    )  # fmt: skip


async def _ended(store: Store, run: RunRecord, status: str = "done", result: str = "Done.") -> RunRecord:
    await store.finish_run(run.id, status, result)  # type: ignore[arg-type]  # a run status, in a test
    return await store.get_run(run.id)


async def test_the_card_lists_what_dot_did_on_its_own_and_never_what_the_owner_started_and_waited_for(
    tmp_path: Path,
) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        since: float = time.time() - 60
        agent = await store.create_agent("Scout", "Finds leads", "", "m:m", None, None, (), None, None)
        plain: ThreadRecord = await store.create_thread("Deploy the site", "ui")
        agents_chat: ThreadRecord = await store.create_thread("Scout", "ui", None, agent.id)
        schedule = await store.add_schedule(
            "nightly build", "0 3 * * *", None, "Look.", "task", time.time() + 86_400, UNSET_OPTIONS, None, True
        )
        schedule_chat: ThreadRecord = await store.create_thread("nightly build · now", "schedule")
        await store.link_schedule_thread(schedule_chat.id, schedule.id)

        scheduled: RunRecord = await _run(store, schedule_chat, "schedule")
        listed: dict[str, RunRecord] = {
            "schedule": scheduled,
            "reply": await _run(store, plain, "reply"),
            # An agent's work is sent by a run (``message_agent``), so it always has a parent.
            "team": await _run(store, agents_chat, "team", scheduled.id),
            "background": await _run(store, plain, "background"),
            # What a watcher set off and what Dot did on its own initiative: nobody watches them either.
            "watcher": await _run(store, plain, "watcher"),
            "initiative": await _run(store, plain, "initiative"),
            # An agent's own chat, under a source that is no owner's: nobody started it and nobody waited.
            "work_order": await _run(store, agents_chat, "work_order"),
        }
        left_out: dict[str, RunRecord] = {
            "ui, any chat": await _run(store, plain, "ui"),
            "ui, agent's chat": await _run(store, agents_chat, "ui"),
            "cli, agent's chat": await _run(store, agents_chat, "cli"),
            # What goes with a run the owner started: a review of theirs, an eval, an agent's welcome (it opens in
            # front of them), and a run resumed after one they started.
            "review, agent's chat": await _run(store, agents_chat, "review"),
            "eval, agent's chat": await _run(store, agents_chat, "eval"),
            "welcome, agent's chat": await _run(store, agents_chat, "welcome"),
            "resume, agent's chat": await _run(store, agents_chat, "resume"),
            # A source no rule names, in a chat that is no agent's.
            "work order, plain chat": await _run(store, plain, "work_order"),
            "notice, plain chat": await _run(store, plain, "notice"),
        }
        helper: RunRecord = await _run(store, agents_chat, "delegate", listed["team"].id)
        for run in (*listed.values(), *left_out.values(), helper):
            await _ended(store, run)
        view: AwayView = await read_away(store, since, time.time() + 1)
        assert {run.run_id for run in view.runs} == {run.id for run in listed.values()}
        assert view.done == 7 and view.failed == 0 and view.working == 0 and view.campaign_sessions == 0
        by_source: dict[str, str] = {run.source: run.title for run in view.runs}
        # A run of a schedule is named by it; any other by its chat, and an agent's by the chat it is with.
        assert by_source["schedule"] == "nightly build"
        assert by_source["reply"] == by_source["background"] == by_source["watcher"] == "Deploy the site"
        assert by_source["team"] == by_source["work_order"] == "Scout"
        # The rule is the sources nobody watches (named once, in ``proactive``) with an agent's work and a command that
        # ended in the background; and the owner's sources, with a welcome and a resume.
        assert set(AWAY_RULE.sources) == {
            "schedule", "watcher", "initiative", "reply", "team", "background",
        }  # fmt: skip
        assert set(AWAY_RULE.owners) == {"ui", "cli", "review", "eval", "welcome", "resume"}
        assert AWAY_RULE.delegated == ("team",)
    finally:
        await store.close()


async def test_an_agents_work_that_a_run_sent_is_listed_unless_the_owners_own_run_sent_it_and_counts_for_itself(
    tmp_path: Path,
) -> None:
    """An agent's work always has a parent (the run that sent it, ``message_agent``): it is listed all the same, as a
    row of its own, unless a run of the owner's is above it, however many runs it went through (they wait for that run,
    and for the work in it); a plain helper is none. Its decisions and its drafts count, under itself, and a helper's
    under the run that has it."""
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        since: float = time.time() - 60
        agent = await store.create_agent("Scout", "Finds leads", "", "m:m", None, None, (), None, None)
        plain: ThreadRecord = await store.create_thread("Deploy the site", "ui")
        agents_chat: ThreadRecord = await store.create_thread("Scout", "ui", None, agent.id)
        by_dot: RunRecord = await _run(store, plain, "reply")
        mine: RunRecord = await _run(store, plain, "ui")
        sent_by_dot: RunRecord = await _run(store, agents_chat, "team", by_dot.id)
        sent_by_me: RunRecord = await _run(store, agents_chat, "team", mine.id)
        # An agent that sends work to another agent is no owner's run either.
        sent_on: RunRecord = await _run(store, agents_chat, "team", sent_by_dot.id)
        helper: RunRecord = await _run(store, plain, "delegate", by_dot.id)
        # Work that went through an agent, or through a helper, from the owner's run is the owner's work too.
        chained: RunRecord = await _run(store, agents_chat, "team", sent_by_me.id)
        mine_helper: RunRecord = await _run(store, plain, "delegate", mine.id)
        through_helper: RunRecord = await _run(store, agents_chat, "team", mine_helper.id)
        for run, decided_by in ((by_dot, "reviewer"), (sent_by_dot, "yolo"), (sent_by_me, "yolo"), (sent_on, "yolo"),
                                (helper, "yolo"), (chained, "yolo"), (through_helper, "yolo")):  # fmt: skip
            asked = await store.create_approval(run.id, "shell", "x", "high", {})
            await store.decide_approval(asked.id, "approved", decided_by, "")
        await store.add_draft(sent_by_dot.id, "mail", ["a@example.com"], [], "Offer", "x")
        await store.add_draft(sent_by_me.id, "mail", ["b@example.com"], [], "Mine", "x")
        for run in (by_dot, mine, sent_by_dot, sent_by_me, sent_on, helper, chained, mine_helper, through_helper):
            await _ended(store, run)
        view: AwayView = await read_away(store, since, time.time() + 1)
        assert {run.run_id for run in view.runs} == {by_dot.id, sent_by_dot.id, sent_on.id}
        assert {run.run_id: run.title for run in view.runs}[sent_by_dot.id] == "Scout"
        assert view.done == 3
        # The work the owner's run sent and that run itself are no one's news; the helper counts for the reply.
        counted: dict[str, tuple[int, int]] = {run.run_id: (run.yolo, run.reviewer) for run in view.decision_runs}
        assert counted == {by_dot.id: (1, 1), sent_by_dot.id: (1, 0), sent_on.id: (1, 0)}
        assert (view.decisions.yolo_approved, view.decisions.reviewer_approved) == (3, 1)
        assert view.drafts == 1
    finally:
        await store.close()


async def test_a_run_that_works_counts_as_working_and_one_that_ended_before_the_moment_is_not_news(
    tmp_path: Path,
) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        plain: ThreadRecord = await store.create_thread("Deploy the site", "ui")
        # The run that sent the agent's work, over before the moment, so that it is no news itself.
        sender: RunRecord = await _ended(store, await _run(store, plain, "initiative"))
        old: RunRecord = await _run(store, plain, "team", sender.id)
        await _ended(store, old)
        await asyncio.sleep(0.02)
        since: float = time.time()
        await asyncio.sleep(0.02)
        working: RunRecord = await _run(store, plain, "background")
        failed: RunRecord = await _ended(store, await _run(store, plain, "reply"), "failed", "The site moved.")
        stopped: RunRecord = await _ended(store, await _run(store, plain, "team", sender.id), "cancelled", "")
        view: AwayView = await read_away(store, since, time.time() + 1)
        assert {run.run_id for run in view.runs} == {working.id, failed.id, stopped.id}
        assert (view.done, view.failed, view.cancelled, view.working) == (0, 1, 1, 1)
        statuses: dict[str, str] = {run.run_id: run.status for run in view.runs}
        assert statuses[working.id] in ("queued", "running", "blocked")
        assert next(run for run in view.runs if run.run_id == failed.id).answer == "The site moved."
    finally:
        await store.close()


async def test_the_decisions_and_the_drafts_of_the_new_runs_and_their_helpers_are_counted_and_a_session_stays_a_session(
    tmp_path: Path,
) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        since: float = time.time() - 60
        plain: ThreadRecord = await store.create_thread("Deploy the site", "ui")
        reply: RunRecord = await _run(store, plain, "reply")
        helper: RunRecord = await _run(store, plain, "delegate", reply.id)
        mine: RunRecord = await _run(store, plain, "ui")
        for run, decided_by in ((reply, "yolo"), (helper, "reviewer"), (mine, "yolo")):
            asked = await store.create_approval(run.id, "shell", "x", "high", {})
            await store.decide_approval(asked.id, "approved", decided_by, "")
        await store.add_draft(helper.id, "mail", ["a@example.com"], [], "Hi", "x")
        await store.add_draft(mine.id, "mail", ["b@example.com"], [], "Mine", "x")
        goal_chat: ThreadRecord = await store.create_thread("Ship 2.0", "ui")
        campaign = await store.create_campaign(
            "Ship 2.0", "Ship it.", goal_chat.id, "m:m", "high", "standard", None, "yolo", [], "own", ()
        )
        session: RunRecord = await store.create_run(
            goal_chat.id, "Ship it.", "task", "m:m", None, "schedule", "high", "standard", None, "yolo", False,
            campaign.id, None, None,
        )  # fmt: skip
        for run in (reply, helper, mine, session):
            await _ended(store, run)
        view: AwayView = await read_away(store, since, time.time() + 1)
        assert {run.run_id for run in view.runs} == {reply.id, session.id}
        assert view.campaign_sessions == 1 and view.done == 1
        # The reply's yolo decision and its helper's reviewer decision; not the owner's own run's.
        assert (view.decisions.yolo_approved, view.decisions.reviewer_approved) == (1, 1)
        assert [(run.run_id, run.yolo, run.reviewer) for run in view.decision_runs] == [(reply.id, 1, 1)]
        assert view.drafts == 1
        assert next(run for run in view.runs if run.run_id == session.id).campaign
    finally:
        await store.close()


async def test_the_moment_is_the_one_the_owner_marked_and_a_run_that_ends_after_it_is_news_again(
    tmp_path: Path,
) -> None:
    """Item 1356 holds: the mark is Dot's (``away.read_at``), kept for every device, and the card reads from it."""
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        plain: ThreadRecord = await store.create_thread("Deploy the site", "ui")
        first: RunRecord = await _ended(store, await _run(store, plain, "background"))
        assert await read_mark(store) is None
        marked: float = await mark_read(store, (first.finished_at or time.time()) + 0.001, time.time() + 5)
        assert await read_mark(store) == marked
        await asyncio.sleep(0.02)
        second: RunRecord = await _ended(store, await _run(store, plain, "reply"))
        view: AwayView = await read_away(store, marked, time.time() + 1)
        assert {run.run_id for run in view.runs} == {second.id}
        # A mark that moved on past the second run leaves nothing to tell, and never moves back.
        later: float = await mark_read(store, (second.finished_at or time.time()) + 0.001, time.time() + 5)
        assert (await read_away(store, later, time.time() + 1)).runs == []
        assert await mark_read(store, 1.0, time.time() + 5) == later
    finally:
        await store.close()
