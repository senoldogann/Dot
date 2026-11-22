"""The index of the roadmap items (item 1317, scripts/roadmap_index.py) and its check with the item records."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPTS: Path = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


index = _load("roadmap_index")
records = _load("check_item_records")


def _item(folder: Path, number: int, title: str, status: str = "done (2026-11-21).") -> None:
    (folder / f"{number}.md").write_text(
        f"### {number}. {title}\n\n**Status: {status}** What it did.\n\nLog:\n- item {number}.\n", encoding="utf-8"
    )


class TestEntry:
    def test_reads_the_title_the_first_clause_of_the_status_and_its_date(self) -> None:
        found = index.entry(7, "### 7. A title\n\n**Status: done (2026-11-21); needs the owner's look.** Text.\n")

        assert (found.number, found.title, found.status, found.date) == (7, "A title", "done", "2026-11-21")

    def test_reads_a_status_that_runs_over_two_lines(self) -> None:
        text = "### 7. T\n\n**Status: done (2026-10-23), except what\nthe owner must do.** Text.\n"

        found = index.entry(7, text)

        assert (found.status, found.date) == ("done", "2026-10-23")

    def test_keeps_a_status_of_several_words_to_its_first_clause(self) -> None:
        found = index.entry(7, "### 7. T\n\n**Status: parts A and B done (2026-10-23, a note).** Text.\n")

        assert found.status == "parts A and B done"

    def test_an_item_without_a_status_is_a_row_that_says_so(self) -> None:
        found = index.entry(7, "### 7. T\n\nText only.\n")

        assert (found.status, found.date) == ("no status", "")

    def test_a_file_whose_title_line_names_another_number_is_an_error(self) -> None:
        with pytest.raises(ValueError, match=r"roadmap/7\.md has no '### 7\. <title>' line"):
            index.entry(7, "### 8. T\n")


class TestIndex:
    def test_lists_the_items_newest_first_whatever_files_exist(self, tmp_path: Path) -> None:
        for number in (459, 1292, 1316, 1303):
            _item(tmp_path, number, f"Title {number}")
        (tmp_path / "README.md").write_text("# Roadmap items\n", encoding="utf-8")

        rows = [line for line in index.render(index.entries(tmp_path)).splitlines() if line.startswith("| [")]

        assert [row.split("]")[0] for row in rows] == ["| [1316", "| [1303", "| [1292", "| [459"]
        assert rows[0] == "| [1316](1316.md) | done | 2026-11-21 | Title 1316 |"

    def test_a_bar_in_a_title_does_not_split_its_cell(self) -> None:
        text = index.render([index.Entry(1, "a | b", "done", "2026-11-21")])

        assert "| a \\| b |" in text

    def test_an_index_is_stale_until_it_is_written_again(self, tmp_path: Path) -> None:
        _item(tmp_path, 1315, "One")
        assert not index.is_current(tmp_path)
        (tmp_path / "INDEX.md").write_text(index.render(index.entries(tmp_path)), encoding="utf-8")
        assert index.is_current(tmp_path)

        _item(tmp_path, 1292, "Another branch's item")

        assert not index.is_current(tmp_path)

    def test_the_index_of_the_repository_is_what_its_files_make(self) -> None:
        assert index.is_current(), index.STALE


class TestCheckedWithTheRecords:
    def test_a_stale_index_fails_the_item_records_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(records, "commits", lambda base, head, cwd=None: [])
        monkeypatch.setattr(records, "index_is_stale", lambda: True)

        assert records.main(["--base", "main", "--head", "HEAD"]) == 1

    def test_a_current_index_passes_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(records, "commits", lambda base, head, cwd=None: [])
        monkeypatch.setattr(records, "index_is_stale", lambda: False)

        assert records.main(["--base", "main", "--head", "HEAD"]) == 0
