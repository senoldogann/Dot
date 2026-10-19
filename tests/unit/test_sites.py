"""What runs learned on a site comes back where it was learned (item 308): the sites a call reached, the words a text
names a site by, the tags of a lesson, and which lessons and learned skills a run that reaches a site is handed."""

from pathlib import Path

from dotagent.agent.executor import reached_sites
from dotagent.agent.playbooks import PlaybookEntry, PlaybookMeta
from dotagent.agent.sites import (
    MOST_SITE_LESSONS,
    SiteKnowledge,
    names_site,
    new_sites,
    site_lessons,
    site_note,
    site_skills,
    site_tag,
    site_tags,
    site_words,
)
from dotagent.llm.types import ToolCall
from dotagent.policy.types import ActionInfo, ActionKind
from dotagent.skills.catalog import PLAYBOOK_SOURCE
from dotagent.skills.types import Skill
from dotagent.store.models import MemoryRecord


def _action(tool: str, kind: ActionKind, target: str) -> ActionInfo:
    return ActionInfo(tool=tool, kind=kind, risk="low", summary="", target=target, flags=())


def _call(tool: str) -> ToolCall:
    return ToolCall(id="call_1", name=tool, arguments={})


def _note(identifier: str, agent_id: str | None) -> MemoryRecord:
    return MemoryRecord(
        id=identifier,
        content=f"Lesson {identifier}: the basket is under the account menu on amazon.com.",
        tags=("lesson", "site-amazon-com"),
        created_at=1.0,
        updated_at=1.0,
        source_run="run_0",
        agent_id=agent_id,
        used_count=0,
        last_used_at=None,
        expires_at=None,
        replaced_by=None,
        tainted=False,
    )


def _skill(name: str, source: str) -> Skill:
    return Skill(
        name=name, description=f"Do {name}.", root=Path("/tmp/skills") / name, body="", source=source, icon="sparkle"
    )


def _entry(name: str, host: str, uses: int, retired: bool) -> PlaybookEntry:
    meta: PlaybookMeta = PlaybookMeta(
        name=name,
        kind=name,
        host=host,
        runs=("run_0",),
        acting_steps=(3,),
        written_at=1.0,
        updated_at=1.0,
        uses=uses,
        failures=0,
    )
    return PlaybookEntry(skill=_skill(name, PLAYBOOK_SOURCE), meta=meta, retired=retired)


def test_a_call_reaches_the_host_of_its_page_the_app_it_drives_or_the_apps_a_script_names() -> None:
    assert reached_sites(
        _call("browser_open"), _action("browser_open", "browser_navigate", "https://www.x.com/home")
    ) == ("www.x.com",)
    assert reached_sites(_call("desktop_act"), _action("desktop_act", "desktop_input", "Notes")) == ("Notes",)
    script: str = 'tell application "Mail" to get name of every mailbox\ntell application "Notes" to activate'
    assert reached_sites(_call("apple_script"), _action("apple_script", "app_control", script)) == ("Mail", "Notes")
    assert reached_sites(_call("shell"), _action("shell", "shell", "ls ~/Desktop")) == ()
    assert reached_sites(_call("desktop_apps"), _action("desktop_apps", "desktop_observe", "")) == ()
    assert reached_sites(_call("read_file"), None) == ()


def test_a_site_is_named_by_its_host_and_its_main_label_and_an_app_by_its_name() -> None:
    assert site_words("www.amazon.com.tr") == ("amazon.com.tr", "amazon")
    assert site_words("mail.google.com") == ("mail.google.com", "google")
    # A main label this short names too many things to be matched alone.
    assert site_words("www.bbc.co.uk") == ("bbc.co.uk",)
    assert site_words("Notes") == ("notes",)
    assert names_site("On Amazon the basket is under the account menu.", "www.amazon.com.tr")
    assert names_site("Open amazon.com.tr/orders first.", "www.amazon.com.tr")
    assert not names_site("The googleapis host serves the fonts.", "mail.google.com")
    assert names_site("Keep the list in Notes, not in Reminders.", "Notes")
    assert not names_site("Keep the list in Reminders.", "Notes")


def test_a_run_reaches_a_site_once_however_its_name_is_written() -> None:
    reached: list[str] = ["www.x.com", "x.com", "Notes", "y.com", "notes", "www.z.org"]
    assert new_sites(reached, {site_tag("y.com")}) == ("www.x.com", "Notes", "www.z.org")
    assert new_sites(["X.com"], {site_tag("www.x.com")}) == ()


def test_a_lesson_is_tagged_with_the_sites_of_its_run_it_names_each_once() -> None:
    assert site_tag("www.amazon.com") == "site-amazon-com"
    assert site_tag("Notes") == "site-notes"
    sites: list[str] = ["www.amazon.com", "Notes", "www.ebay.com", "amazon.com"]
    lesson: str = "On Amazon the basket is under the account menu; keep the order number in Notes."
    assert site_tags(lesson, sites) == ("site-amazon-com", "site-notes")
    assert site_tags("Read the file before you change it.", sites) == ()


def test_a_run_is_handed_a_sites_lessons_it_was_not_handed_that_rest_not_and_are_its_own_to_read() -> None:
    notes: list[MemoryRecord] = [_note(f"mem_{index}", None) for index in range(MOST_SITE_LESSONS + 3)]
    theirs: MemoryRecord = _note("mem_agent", "agent_1")
    handed: tuple[MemoryRecord, ...] = site_lessons([theirs, *notes], {"mem_0"}, None, {"mem_1"})
    assert [note.id for note in handed] == [f"mem_{index}" for index in range(2, 2 + MOST_SITE_LESSONS)]
    # The agent's own lesson goes to its runs.
    assert site_lessons([theirs], set(), "agent_1", set()) == (theirs,)
    # A lesson saved while reading outside content is left to memory_search (item 384).
    marked: MemoryRecord = _note("mem_marked", None).model_copy(update={"tainted": True})
    assert site_lessons([marked, notes[2]], set(), None, set()) == (notes[2],)


def test_a_run_is_offered_the_learned_skills_of_the_site_its_instructions_do_not_list_the_most_used_first() -> None:
    entries: list[PlaybookEntry] = [
        _entry("buy-a-book", "www.amazon.com", 2, False),
        _entry("track-an-order", "amazon.com", 7, False),
        _entry("old-way", "www.amazon.com", 9, True),
        _entry("listed-one", "www.amazon.com", 5, False),
        _entry("not-offered", "www.amazon.com", 5, False),
        _entry("elsewhere", "www.ebay.com", 5, False),
    ]
    offered: list[Skill] = [
        _skill(name, PLAYBOOK_SOURCE) for name in ("buy-a-book", "track-an-order", "old-way", "listed-one", "elsewhere")
    ]
    found: tuple[Skill, ...] = site_skills(entries, "WWW.Amazon.com", offered, {"listed-one"})
    assert [skill.name for skill in found] == ["track-an-order", "buy-a-book"]


def test_the_note_says_where_each_lesson_and_skill_was_learned() -> None:
    note: str = site_note(
        [SiteKnowledge(site="www.amazon.com", lessons=(_note("mem_1", None),), skills=(_skill("buy-a-book", "x"),))]
    )
    assert note.startswith("[system note] Earlier runs learned this where this run just arrived.")
    assert "On www.amazon.com:\n- [mem_1] Lesson mem_1: the basket is under the account menu" in note
    assert "- Learned skill buy-a-book: Do buy-a-book. (read it with skill before you act there)" in note
