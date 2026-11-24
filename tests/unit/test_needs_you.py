"""What waits on the owner, in one list (item 1422): the approvals a run waits for, its questions with their options
(and the one it recommended), the requests for a secret (who asks, never what), the hand-offs, and what a run left for
the owner; newest first, with a count. The records are the store's own, and the runs at work are named by the test."""

import json
import time
from pathlib import Path

from dotagent.core.types import JsonObject
from dotagent.notify.needs_you import (
    SECRET_TITLE,
    TITLE_CHARS,
    NeedsYou,
    needs_you_count,
    open_asks,
    read_needs_you,
)
from dotagent.store.db import Store
from dotagent.store.models import RunRecord

_NOW: float = time.time()


async def _run(store: Store, goal: str, parent: str | None = None, agent_id: str | None = None) -> RunRecord:
    thread = await store.create_thread(goal, "chat")
    return await store.create_run(
        thread.id, goal, "task", "m:m", parent, "chat", "medium", "standard", None, "ask", False, None, agent_id, None
    )


def _question(options: list[JsonObject] | None = None) -> JsonObject:
    return {"question": "Which one?", "options": options or [], "multiple": False}


async def test_what_waits_on_the_owner_is_one_list_newest_first_with_its_kinds_and_a_count(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        run: RunRecord = await _run(store, "Find leads")
        agent = await store.create_agent("Scout", "Finds leads", "", "m:m", None, None, (), None, None)
        helper_of: RunRecord = await _run(store, "Write the offer", None, agent.id)
        helper: RunRecord = await _run(store, "Look it up", helper_of.id)
        live: dict[str, RunRecord] = {run.id: run, helper_of.id: helper_of, helper.id: helper}
        options: list[JsonObject] = [
            {"label": "Both", "description": "Do the two", "recommended": False},
            {"label": "The first", "description": None, "recommended": True},
        ]
        approval = await store.create_approval(run.id, "shell", "Run `make deploy` — it ships", "medium", {})
        ask = await store.create_approval(helper_of.id, "ask_user", "Which one?", "question", _question(options))
        free = await store.create_approval(run.id, "ask_user", "What name?", "question", _question())
        secret = await store.create_approval(
            run.id, "ask_secret", "Cloudflare token", "secret", {"name": "CF", "label": "Cloudflare token", "why": "x"}
        )
        handoff = await store.create_approval(helper.id, "browser_act", "Pay the invoice", "handoff", {})
        # What is decided, and what a run that no longer works here asked, is not listed.
        decided = await store.create_approval(run.id, "shell", "Old", "low", {})
        await store.decide_approval(decided.id, "approved", "ui", "")
        gone: RunRecord = await _run(store, "Ended")
        await store.create_approval(gone.id, "shell", "Left over from before a restart", "low", {})

        listed: NeedsYou = await read_needs_you(store, live, _NOW)
        assert listed.count == 5 == len(listed.items) == await needs_you_count(store, live, _NOW)
        newest_first: list[str] = [item.id for item in listed.items]
        assert newest_first == [handoff.id, secret.id, free.id, ask.id, approval.id]
        by_id = {item.id: item for item in listed.items}
        assert by_id[approval.id].kind == "approval" and by_id[approval.id].agent == "Dot"
        assert by_id[approval.id].title == "Run `make deploy` — it ships".replace("`", "")
        assert by_id[approval.id].choices is None and by_id[approval.id].recommended is None
        assert by_id[approval.id].run_id == run.id and by_id[approval.id].thread_id == run.thread_id
        assert by_id[approval.id].since == approval.created_at
        # A question carries its options, and the label of the one the run recommended.
        assert by_id[ask.id].kind == "question" and by_id[ask.id].agent == "Scout"
        assert by_id[ask.id].choices == ("Both", "The first") and by_id[ask.id].recommended == "The first"
        # A question with no options asks for words: no choices, and nothing recommended.
        assert by_id[free.id].choices is None and by_id[free.id].recommended is None
        # A hand-off of a helper's run is read in the chat of the run at the top.
        assert by_id[handoff.id].kind == "handoff" and by_id[handoff.id].thread_id == helper_of.thread_id
        assert by_id[handoff.id].run_id == helper.id
        # The secret is told by its id, who asks and a fixed title: nothing of the card's words.
        assert by_id[secret.id].kind == "secret" and by_id[secret.id].title == SECRET_TITLE
        assert by_id[secret.id].choices is None
        assert "Cloudflare" not in json.dumps(by_id[secret.id].model_dump())
    finally:
        await store.close()


async def test_a_question_with_options_none_of_which_is_recommended_names_none_and_a_title_is_one_masked_line(
    tmp_path: Path,
) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        run: RunRecord = await _run(store, "Find leads")
        live: dict[str, RunRecord] = {run.id: run}
        plain: list[JsonObject] = [{"label": "Yes", "description": None, "recommended": False}, {"label": "No"}]
        await store.create_approval(run.id, "ask_user", "Go on?", "question", _question(plain))
        token: str = "ghp_" + "a1B2c3D4e5" * 4
        long_summary: str = f"First line with {token}\nsecond line\n" + "x" * 300
        await store.create_approval(run.id, "shell", long_summary, "high", {})
        listed: NeedsYou = await read_needs_you(store, live, _NOW)
        question, step = listed.items[1], listed.items[0]
        assert question.choices == ("Yes", "No") and question.recommended is None
        assert step.kind == "approval" and "\n" not in step.title and token not in step.title
        assert len(step.title) <= TITLE_CHARS
        # Left out of the JSON when there is none: the app reads a missing key as no choice.
        dumped: JsonObject = listed.model_dump(exclude_none=True)
        items: object = dumped["items"]
        assert isinstance(items, tuple) and "choices" not in items[0] and "recommended" not in items[1]
    finally:
        await store.close()


async def test_what_a_run_left_for_the_owner_is_listed_while_it_waits_even_when_its_run_is_over(tmp_path: Path) -> None:
    """A question a run went on without, and a step it kept for the owner, wait until they answer, whether or not the
    run still works; once answered they are no longer asks."""
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        run: RunRecord = await _run(store, "Brief")
        await store.finish_run(run.id, "done", "x")
        left = await store.create_left_approval(run.id, "ask_user", "Which day?", "question", _question())
        kept = await store.create_kept_approval(
            run.id, "browser_act", "Sign in to the bank", "handoff", {}, "digest", 9e9, run.thread_id, None
        )
        secret = await store.create_left_approval(
            run.id, "ask_secret", "A key", "secret", {"name": "K", "label": "A key"}
        )
        asks = await open_asks(store, {})
        assert sorted(ask.id for ask, _ in asks) == sorted([left.id, kept.id, secret.id])
        listed: NeedsYou = await read_needs_you(store, {}, _NOW)
        assert {item.kind for item in listed.items} == {"question", "handoff", "secret"} and listed.count == 3
        await store.decide_approval(left.id, "approved", "ui", "Friday")
        assert (await read_needs_you(store, {}, _NOW)).count == 2
    finally:
        await store.close()


async def test_a_step_kept_for_the_owner_whose_time_is_over_is_no_longer_theirs_to_answer(tmp_path: Path) -> None:
    """A kept step lapses at ``kept_until`` though nothing has closed its card yet; the list and the count leave it out
    from then on, as the answer route refuses it."""
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        run: RunRecord = await _run(store, "Brief")
        await store.finish_run(run.id, "done", "x")
        kept = await store.create_kept_approval(
            run.id, "browser_act", "Sign in", "handoff", {}, "d1", _NOW + 100, run.thread_id, None
        )
        assert [item.id for item in (await read_needs_you(store, {}, _NOW)).items] == [kept.id]
        assert [item.id for item in (await read_needs_you(store, {}, _NOW + 99.9)).items] == [kept.id]
        assert (await read_needs_you(store, {}, _NOW + 100)).count == 0
        assert await needs_you_count(store, {}, _NOW + 100) == 0
    finally:
        await store.close()
