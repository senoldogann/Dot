"""Playbooks: the site a run acted on, the digest the writer reads, the skill folder a playbook becomes, and the record
that retires one after two failed runs."""

from pathlib import Path

import pytest

from dotagent.agent.playbooks import (
    MAX_FAILURES,
    RETIRED_DIR,
    Playbook,
    PlaybookMeta,
    acting_digest,
    acting_steps,
    drop_playbook,
    list_playbooks,
    main_site,
    playbook_name,
    read_meta,
    record_use,
    render_playbook,
    slug,
    used_playbooks,
    write_playbook,
)
from dotagent.agent.review import PlaybookDraft
from dotagent.core.errors import NotFoundError
from dotagent.core.types import JsonObject
from dotagent.skills.catalog import SkillRoot, load_catalog
from dotagent.skills.parse import parse_skill
from dotagent.skills.types import SKILL_FILE, Skill, SkillCatalog
from dotagent.store.models import EventRecord

HOST: str = "shop.example.com"


def _event(index: int, kind: str, data: JsonObject) -> EventRecord:
    return EventRecord(id=index, run_id="run-1", ts=float(index), kind=kind, data=data)


def _call(index: int, call_id: str, tool: str, args: JsonObject) -> EventRecord:
    return _event(index, "tool_call", {"call_id": call_id, "tool": tool, "args": args})


def _result(index: int, call_id: str, tool: str, site: str, ok: bool, text: str) -> EventRecord:
    return _event(index, "tool_result", {"call_id": call_id, "tool": tool, "site": site, "ok": ok, "text": text})


EVENTS: tuple[EventRecord, ...] = (
    _call(1, "c1", "browser_open", {"url": f"https://{HOST}/search"}),
    _result(2, "c1", "browser_open", HOST, True, "Opened the search page"),
    _call(3, "c2", "browser_act", {"steps": [{"action": "fill", "ref": "e5", "text": "lamp"}]}),
    _result(4, "c2", "browser_act", HOST, False, "e5 is not a text field"),
    _call(5, "c3", "browser_act", {"steps": [{"action": "fill", "ref": "e7", "text": "lamp"}]}),
    _result(6, "c3", "browser_act", HOST, True, "12 results"),
    _call(7, "c4", "browser_open", {"url": "https://other.example.org/"}),
    _result(8, "c4", "browser_open", "other.example.org", True, "Opened"),
    _call(9, "c5", "write_file", {"path": "prices.csv"}),
    _result(10, "c5", "write_file", "", True, "wrote 3 lines"),
    _call(11, "c6", "skill", {"name": "playbook-shop-example-com"}),
    _result(12, "c6", "skill", "", True, "Skill playbook-shop-example-com"),
    _call(13, "c7", "skill", {"name": "x-twitter-growth"}),
)


def _draft() -> PlaybookDraft:
    return PlaybookDraft(
        description='Search a product on shop.example.com and read its "price" from the first result.',
        when="A goal that asks for the price of a product on shop.example.com.",
        steps=("Open https://shop.example.com/search.", "Fill the search field (ref e7) with <product>."),
        parameters=("<product>: the product to search for",),
    )


def _playbook() -> Playbook:
    return Playbook(
        name=playbook_name(HOST),
        host=HOST,
        draft=_draft(),
        run_id="run-1",
        goal="Find the price of a lamp on shop.example.com",
        written_at=1_700_000_000.0,
        acting_steps=3,
    )


def test_the_main_site_is_the_one_most_results_name_and_its_steps_make_the_digest() -> None:
    assert main_site(EVENTS) == HOST
    assert main_site(EVENTS[8:]) == ""
    steps = acting_steps(EVENTS, HOST)
    assert [(step.tool, step.ok) for step in steps] == [
        ("browser_open", True),
        ("browser_act", False),
        ("browser_act", True),
    ]
    digest: str = acting_digest(steps)
    assert digest.startswith('1. browser_open {"url":"https://shop.example.com/search"} -> ok: Opened the search page')
    assert "2. browser_act " in digest and "-> FAILED: e5 is not a text field" in digest
    assert used_playbooks(EVENTS) == ("playbook-shop-example-com",)


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("shop.example.com", "shop-example-com"),
        ("Safari", "safari"),
        ("Visual Studio Code", "visual-studio-code"),
        ("", ""),
    ],
)
def test_a_host_or_application_becomes_a_skill_name(host: str, expected: str) -> None:
    assert slug(host) == expected


def test_a_playbook_is_a_skill_the_catalog_loads(tmp_path: Path) -> None:
    file: Path = write_playbook(tmp_path, _playbook())
    skill: Skill = parse_skill(file, file.read_text(encoding="utf-8"), "playbook")
    assert skill.name == "playbook-shop-example-com"
    assert skill.description == "Search a product on shop.example.com and read its 'price' from the first result."
    assert (
        "## Steps\n\n1. Open https://shop.example.com/search.\n2. Fill the search field (ref e7) with <product>."
        in skill.body
    )
    assert "- Run: run-1, verified by its goal check on 2023-11-14" in skill.body
    assert render_playbook(_playbook()).startswith("---\nname: playbook-shop-example-com\n")
    catalog: SkillCatalog = load_catalog((SkillRoot(path=tmp_path, source="playbook"),))
    assert [entry.name for entry in catalog.skills] == ["playbook-shop-example-com"] and catalog.problems == ()
    assert read_meta(file.parent) == PlaybookMeta(
        name="playbook-shop-example-com", host=HOST, run_id="run-1", written_at=1_700_000_000.0, uses=0, failures=0
    )
    with pytest.raises(FileExistsError):
        write_playbook(tmp_path, _playbook())


def test_a_playbook_that_two_runs_read_and_failed_is_retired_and_no_longer_loaded(tmp_path: Path) -> None:
    name: str = _playbook().name
    write_playbook(tmp_path, _playbook())
    assert record_use(tmp_path, name, True).uses == 1
    assert record_use(tmp_path, name, False).failures == 1
    assert (tmp_path / name / SKILL_FILE).is_file()
    meta: PlaybookMeta = record_use(tmp_path, name, False)
    assert meta.failures == MAX_FAILURES
    assert not (tmp_path / name).exists()
    assert (tmp_path / RETIRED_DIR / name / SKILL_FILE).is_file()
    assert load_catalog((SkillRoot(path=tmp_path, source="playbook"),)).skills == ()
    entries = list_playbooks(tmp_path)
    assert [(entry.skill.name, entry.retired, entry.meta.uses) for entry in entries] == [(name, True, 1)]
    assert drop_playbook(tmp_path, name) == tmp_path / RETIRED_DIR / name
    with pytest.raises(NotFoundError):
        drop_playbook(tmp_path, name)
