"""An agent learns for itself (ROADMAP item 94): a lesson, a learned skill and a score carry the agent; its runs are
handed its own and Dot's, never another agent's; a deleted agent leaves its lessons and skills to Dot."""

import time
from pathlib import Path

from dotagent.agent.playbooks import (
    Playbook,
    PlaybookEntry,
    PlaybookMeta,
    list_playbooks,
    offered_skills,
    offered_to,
    playbook_name,
    read_meta,
    release_playbooks,
    share_playbook,
    write_playbook,
)
from dotagent.agent.review import PlaybookDraft
from dotagent.config import load_default_config
from dotagent.core.types import JsonObject
from dotagent.memory.service import REST_TAG, MemoryIndex, SavedMemory, save_memory, visible_to
from dotagent.policy.types import TAINTING_KINDS
from dotagent.skills.catalog import SkillRoot, load_catalog
from dotagent.skills.types import Skill
from dotagent.store.db import Store
from dotagent.store.models import AgentRecord, MemoryRecord, MemoryScore, RunRecord, ThreadRecord

SCOUT: str = "agent_scout"
CLERK: str = "agent_clerk"


def _note(agent_id: str | None) -> MemoryRecord:
    return MemoryRecord(
        id=f"mem_{agent_id}",
        content="A note.",
        tags=("lesson",),
        created_at=1.0,
        updated_at=1.0,
        source_run="run_1",
        agent_id=agent_id,
        used_count=0,
        last_used_at=None,
        expires_at=None,
        replaced_by=None,
        tainted=False,
    )


def test_dots_notes_go_to_every_run_and_an_agents_to_its_own_alone() -> None:
    assert visible_to(_note(None), None) and visible_to(_note(None), SCOUT)
    assert visible_to(_note(SCOUT), SCOUT)
    assert not visible_to(_note(SCOUT), None) and not visible_to(_note(SCOUT), CLERK)


def _playbook(kind: str, agent_id: str | None) -> Playbook:
    return Playbook(
        name=playbook_name(kind),
        kind=kind,
        host="this Mac",
        draft=PlaybookDraft(
            kind=kind,
            description=f"How to {kind.lower()}.",
            when=f"A goal that asks to {kind.lower()}.",
            steps=("Do the first thing.", "Do the second thing."),
            parameters=(),
        ),
        run_id="run-1",
        agent_id=agent_id,
        goal=kind,
        written_at=1_700_000_000.0,
        acting_steps=3,
        tainted=False,
    )


def test_an_agents_skill_is_offered_to_its_runs_alone_until_shared_and_is_dots_once_the_agent_is_gone(
    tmp_path: Path,
) -> None:
    write_playbook(tmp_path, _playbook("Sort the inbox", SCOUT))
    write_playbook(tmp_path, _playbook("Book a table", None))
    entries: tuple[PlaybookEntry, ...] = list_playbooks(tmp_path)
    scouts: PlaybookMeta = next(entry.meta for entry in entries if entry.meta.agent_id == SCOUT)
    assert (scouts.name, scouts.shared) == ("learned-sort-the-inbox", False)
    assert offered_to(scouts, SCOUT) and not offered_to(scouts, None) and not offered_to(scouts, CLERK)
    dots: PlaybookMeta = next(entry.meta for entry in entries if entry.meta.agent_id is None)
    assert offered_to(dots, None) and offered_to(dots, CLERK)
    skills: tuple[Skill, ...] = load_catalog((SkillRoot(path=tmp_path, source="learned"),)).skills
    stranger: Skill = skills[0].model_copy(update={"name": "learned-unrecorded", "source": "learned"})
    home: Skill = skills[0].model_copy(update={"name": "home-skill", "source": "home"})
    assert [skill.name for skill in offered_skills([*skills, stranger, home], entries, SCOUT)] == [
        "learned-book-a-table",
        "learned-sort-the-inbox",
        "home-skill",
    ]
    assert [skill.name for skill in offered_skills([*skills, stranger, home], entries, None)] == [
        "learned-book-a-table",
        "home-skill",
    ]
    assert [skill.name for skill in offered_skills(skills, (), SCOUT)] == []
    # Shared, it goes to every run; kept to the agent again, to its runs alone.
    assert share_playbook(tmp_path, scouts.name, True).shared is True
    assert offered_to(read_meta(tmp_path / scouts.name), CLERK)
    assert share_playbook(tmp_path, scouts.name, False).shared is False
    assert not offered_to(read_meta(tmp_path / scouts.name), CLERK)
    # The agent deleted: its skill is Dot's, and the record keeps the rest.
    assert release_playbooks(tmp_path, SCOUT) == 1
    released: PlaybookMeta = read_meta(tmp_path / scouts.name)
    assert (released.agent_id, released.kind, released.runs) == (None, "Sort the inbox", ("run-1",))
    assert release_playbooks(tmp_path, SCOUT) == 0


async def _run(store: Store, thread: ThreadRecord, agent_id: str | None, status: str) -> RunRecord:
    run: RunRecord = await store.create_run(
        thread.id, "goal", "task", "ollama:m", None, "test", None, "standard", None, "ask", False, None, agent_id, None
    )
    await store.finish_run(run.id, status, "")  # type: ignore[arg-type]
    return await store.get_run(run.id)


async def test_the_store_keeps_an_agents_lessons_and_scores_them_by_its_own_runs(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        scout: AgentRecord = await store.create_agent("Scout", "Finds sources", "", "", None, None, [], None, None)
        thread: ThreadRecord = await store.create_thread("t", "test")
        lesson: MemoryRecord = await store.add_memory("Log in first.", ["lesson"], "run_1", scout.id, None)
        dots: MemoryRecord = await store.add_memory("Dot's note.", [], None, None, None)
        assert lesson.agent_id == scout.id and dots.agent_id is None
        assert await store.agent_memories(scout.id, 10) == [lesson]
        assert (await store.get_memory(lesson.id)).agent_id == scout.id
        own: RunRecord = await _run(store, thread, scout.id, "done")
        other: RunRecord = await _run(store, thread, None, "failed")
        for run in (own, other):
            await store.mark_memories_used(run.id, [lesson.id], time.time(), "start")
            await store.close_memory_uses(run.id, run.status)
        assert await store.memory_scores([lesson.id], 0.0) == {lesson.id: MemoryScore(handed=2, done=1, failed=1)}
        assert await store.agent_memory_scores([lesson.id], 0.0, scout.id) == {
            lesson.id: MemoryScore(handed=1, done=1, failed=0)
        }
        assert await store.agent_memory_scores([lesson.id], 0.0, CLERK) == {}
        assert await store.agent_memory_scores([], 0.0, scout.id) == {}
        # The agent deleted: its lesson stays, as Dot's.
        await store.delete_agent(scout.id)
        assert (await store.get_memory(lesson.id)).agent_id is None
        assert await store.agent_memories(scout.id, 10) == []
    finally:
        await store.close()


async def test_a_text_saved_again_merges_only_into_a_note_the_saver_is_handed(tmp_path: Path) -> None:
    """Audit defect 7: the same text saved by Dot after an agent, or by another agent, merged into the first agent's
    note, which the saver's runs are never handed; a note Dot has is the agent's to merge into."""
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    index: MemoryIndex = MemoryIndex(store=store, embedder=None, config=load_default_config().memory)
    try:
        scout: AgentRecord = await store.create_agent("Scout", "Finds sources", "", "", None, None, [], None, None)
        clerk: AgentRecord = await store.create_agent("Clerk", "Files things", "", "", None, None, [], None, None)
        theirs: SavedMemory = await save_memory(
            index, "Log in before the search.", ("lesson",), "run_1", scout.id, None
        )
        dots: SavedMemory = await save_memory(index, "Log in before the search.", ("lesson",), "run_2", None, None)
        others: SavedMemory = await save_memory(
            index, "Log in before the search.", ("lesson",), "run_3", clerk.id, None
        )
        assert theirs.created and dots.created and not others.created
        assert (theirs.record.agent_id, dots.record.agent_id) == (scout.id, None)
        assert others.record.id == dots.record.id
        # The agent's own note comes first when it and Dot both have the text.
        again: SavedMemory = await save_memory(index, "Log in before the search.", ("lesson",), "run_4", scout.id, None)
        assert again.record.id == theirs.record.id
    finally:
        await store.close()


async def test_a_resting_lesson_saved_again_is_awake(tmp_path: Path) -> None:
    """Audit defect 5: a lesson learnt again kept its rest tag, so no run was handed it although it was "saved"."""
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    index: MemoryIndex = MemoryIndex(store=store, embedder=None, config=load_default_config().memory)
    try:
        first: SavedMemory = await save_memory(index, "Log in before the search.", ("lesson",), "run_1", None, None)
        await store.set_memory_tags(first.record.id, ("lesson", REST_TAG))
        before: float = time.time()
        again: SavedMemory = await save_memory(index, "Log in before the search.", ("lesson",), "run_2", None, None)
        assert again.record.id == first.record.id and not again.created
        assert again.record.tags == ("lesson",) and again.record.updated_at >= before
    finally:
        await store.close()


async def test_a_run_read_outside_when_any_of_its_results_did_not_only_its_newest(tmp_path: Path) -> None:
    """Audit defect 3: the nightly reflection judged a run by its newest 60 results, so a long run that read a web page
    early had its lessons saved as if it had read none."""
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        thread: ThreadRecord = await store.create_thread("t", "test")
        run: RunRecord = await _run(store, thread, None, "done")
        page: JsonObject = {
            "tool": "web_fetch",
            "ok": True,
            "text": "<html>",
            "kind": "network",
            "target": "",
            "channel": "web",
        }
        await store.add_event(run.id, "tool_result", page)
        for _ in range(60):
            await store.add_event(
                run.id,
                "tool_result",
                {
                    "tool": "read_file",
                    "ok": True,
                    "text": "x",
                    "kind": "read",
                    "target": "a",
                    "channel": "",
                    "tainted": False,
                },
            )
        kinds: tuple[str, ...] = tuple(TAINTING_KINDS)
        assert await store.read_outside(run.id, kinds, ("web", "browser"))
        # A run whose results read only this Mac read nothing outside; one that was tainted when a result came did.
        clean: RunRecord = await _run(store, thread, None, "done")
        await store.add_event(
            clean.id, "tool_result", {"tool": "read_file", "kind": "read", "channel": "", "tainted": False}
        )
        assert not await store.read_outside(clean.id, kinds, ("web", "browser"))
        await store.add_event(
            clean.id, "tool_result", {"tool": "read_file", "kind": "read", "channel": "", "tainted": True}
        )
        assert await store.read_outside(clean.id, kinds, ("web", "browser"))
    finally:
        await store.close()
