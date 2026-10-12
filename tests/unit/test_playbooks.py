"""Playbooks: the site a run acted on, the digest the writer reads, the skill folder a playbook becomes, and the record
that retires one after two failed runs."""

from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

import pytest

from dotagent.agent.playbooks import (
    MAX_FAILURES,
    RETIRED_DIR,
    Playbook,
    PlaybookMeta,
    acting_digest,
    acting_steps,
    distils,
    drop_playbook,
    list_playbooks,
    main_site,
    playbook_name,
    read_meta,
    record_use,
    render_playbook,
    same_kind,
    slug,
    update_playbook,
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
# Runs that end at once and count their use of one playbook, and how many uses each counts.
_COUNTERS: int = 8
_COUNTS: int = 25


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
    _call(11, "c6", "skill", {"name": "learned-find-a-product-s-price"}),
    _result(12, "c6", "skill", "", True, "Skill learned-find-a-product-s-price"),
    _call(13, "c7", "skill", {"name": "x-twitter-growth"}),
)


KIND: str = "Find a product's price"


def _draft() -> PlaybookDraft:
    return PlaybookDraft(
        kind=KIND,
        description='Search a product on shop.example.com and read its "price" from the first result.',
        when="A goal that asks for the price of a product on shop.example.com.",
        steps=("Open https://shop.example.com/search.", "Fill the search field (ref e7) with <product>."),
        parameters=("<product>: the product to search for",),
    )


def _playbook() -> Playbook:
    return Playbook(
        name=playbook_name(KIND),
        kind=KIND,
        host=HOST,
        draft=_draft(),
        run_id="run-1",
        agent_id=None,
        goal="Find the price of a lamp on shop.example.com",
        written_at=1_700_000_000.0,
        acting_steps=3,
    )


def test_the_main_site_is_the_one_most_results_name_and_every_acting_call_makes_the_digest() -> None:
    assert main_site(EVENTS) == HOST
    assert main_site(EVENTS[8:]) == ""
    steps = acting_steps(EVENTS)
    # The skill reads are not acting; the write on this Mac and the other site's call are.
    assert [(step.tool, step.ok) for step in steps] == [
        ("browser_open", True),
        ("browser_act", False),
        ("browser_act", True),
        ("browser_open", True),
        ("write_file", True),
    ]
    digest: str = acting_digest(steps)
    assert digest.startswith('1. browser_open {"url":"https://shop.example.com/search"} -> ok: Opened the search page')
    assert "2. browser_act " in digest and "-> FAILED: e5 is not a text field" in digest
    assert used_playbooks(EVENTS) == ("learned-find-a-product-s-price",)
    assert distils("done", len(steps), 4) and not distils("done", len(steps), 6) and not distils("failed", 9, 4)


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("shop.example.com", "shop-example-com"),
        ("Safari", "safari"),
        ("Visual Studio Code", "visual-studio-code"),
        ("", ""),
    ],
)
def test_a_kind_of_work_becomes_a_skill_name(host: str, expected: str) -> None:
    assert slug(host) == expected


def test_a_playbook_is_a_skill_the_catalog_loads(tmp_path: Path) -> None:
    file: Path = write_playbook(tmp_path, _playbook())
    skill: Skill = parse_skill(file, file.read_text(encoding="utf-8"), "learned")
    assert skill.name == "learned-find-a-product-s-price"
    assert skill.description == "Search a product on shop.example.com and read its 'price' from the first result."
    assert (
        "## Steps\n\n1. Open https://shop.example.com/search.\n2. Fill the search field (ref e7) with <product>."
        in skill.body
    )
    assert "## Kind of work\n\nFind a product's price\n" in skill.body
    assert "verified on 2023-11-14" in skill.body and "- Run run-1: 3 acting steps" in skill.body
    assert render_playbook(_playbook(), None).startswith("---\nname: learned-find-a-product-s-price\n")
    catalog: SkillCatalog = load_catalog((SkillRoot(path=tmp_path, source="learned"),))
    assert [entry.name for entry in catalog.skills] == ["learned-find-a-product-s-price"] and catalog.problems == ()
    assert read_meta(file.parent) == PlaybookMeta(
        name="learned-find-a-product-s-price",
        kind=KIND,
        host=HOST,
        runs=("run-1",),
        acting_steps=(3,),
        written_at=1_700_000_000.0,
        updated_at=1_700_000_000.0,
        uses=0,
        failures=0,
        agent_id=None,
        shared=False,
    )
    with pytest.raises(FileExistsError):
        write_playbook(tmp_path, _playbook())
    # A later run of the same kind updates the skill: its steps, every run in the evidence, the record kept.
    later: Playbook = _playbook().model_copy(
        update={"run_id": "run-2", "acting_steps": 2, "written_at": 1_700_100_000.0}
    )
    record_use(tmp_path, later.name, True)
    update_playbook(tmp_path, later.name, later)
    body: str = file.read_text(encoding="utf-8")
    assert "(2 runs, the last one verified on 2023-11-16)" in body
    assert "- Run run-1: 3 acting steps\n- Run run-2: 2 acting steps\n" in body
    meta: PlaybookMeta = read_meta(file.parent)
    assert (meta.runs, meta.acting_steps, meta.uses, meta.updated_at) == (
        ("run-1", "run-2"),
        (3, 2),
        1,
        1_700_100_000.0,
    )
    entries = list_playbooks(tmp_path)
    assert same_kind("find a PRODUCT'S price", entries, None) is entries[0]
    assert same_kind("audit an X profile", entries, None) is None
    # By meaning: the first vector is the new kind's, the rest the entries' in order.
    assert same_kind("price of a product", entries, [(1.0, 0.0), (0.9, 0.1)]) is entries[0]
    assert same_kind("price of a product", entries, [(1.0, 0.0), (0.0, 1.0)]) is None


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
    assert load_catalog((SkillRoot(path=tmp_path, source="learned"),)).skills == ()
    entries = list_playbooks(tmp_path)
    assert [(entry.skill.name, entry.retired, entry.meta.uses) for entry in entries] == [(name, True, 1)]
    assert drop_playbook(tmp_path, name) == tmp_path / RETIRED_DIR / name
    with pytest.raises(NotFoundError):
        drop_playbook(tmp_path, name)


@pytest.mark.parametrize("name", ["..", ".", RETIRED_DIR, "learned-x/../..", "learned-x/../../home", ""])
def test_a_name_that_is_not_one_playbook_folder_is_not_found_and_nothing_is_removed(name: str, tmp_path: Path) -> None:
    playbooks: Path = tmp_path / "home" / "playbooks"
    write_playbook(playbooks, _playbook())
    (playbooks / RETIRED_DIR).mkdir()
    with pytest.raises(NotFoundError):
        drop_playbook(playbooks, name)
    with pytest.raises(NotFoundError):
        record_use(playbooks, name, False)
    assert (playbooks / playbook_name(KIND) / SKILL_FILE).is_file()
    assert (playbooks / RETIRED_DIR).is_dir()


def test_uses_counted_at_once_are_all_kept_and_a_listing_meanwhile_reads_whole_records(tmp_path: Path) -> None:
    """Item 144: runs that end at once each count their use of a playbook, and a listing made meanwhile reads every
    record whole."""
    name: str = _playbook().name
    write_playbook(tmp_path, _playbook())

    def count() -> None:
        for _ in range(_COUNTS):
            record_use(tmp_path, name, True)

    def listing() -> None:
        for _ in range(_COUNTS * _COUNTERS):
            list_playbooks(tmp_path)

    with ThreadPoolExecutor(max_workers=_COUNTERS + 1) as pool:
        done: list[Future[None]] = [pool.submit(count) for _ in range(_COUNTERS)] + [pool.submit(listing)]
    for future in done:
        future.result()
    assert read_meta(tmp_path / name).uses == _COUNTERS * _COUNTS
