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
from dotagent.memory.service import visible_to
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
        thread.id, "goal", "task", "ollama:m", None, "test", None, "standard", "ask", False, None, agent_id, None
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
