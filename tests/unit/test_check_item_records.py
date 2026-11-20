"""The check that a commit writes its own item's records only (item 1265, scripts/check_item_records.py)."""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path
from types import ModuleType

_SCRIPTS: Path = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


records = _load("check_item_records")


class TestItemNumbers:
    def test_reads_the_number_of_one_item(self) -> None:
        assert records.item_numbers("Item 1265: a phone's home names its agents") == {1265}

    def test_reads_every_number_of_a_commit_that_names_several(self) -> None:
        assert records.item_numbers("Items 1262, 1263 and 1264: the numbers main took") == {1262, 1263, 1264}

    def test_reads_no_item_of_a_commit_that_names_none(self) -> None:
        assert records.item_numbers("Merge pull request #1011 from senoldogann/feat/sidebar-order") == set()
        assert records.item_numbers("A fix without a number") == set()

    def test_reads_no_number_after_the_colon(self) -> None:
        assert records.item_numbers("Item 1265: after 1299 things") == {1265}


class TestOffences:
    def test_a_commit_that_writes_its_own_records_is_no_offence(self) -> None:
        assert records.offences([("Item 1265: a phone's home", ["changes/1265.md", "roadmap/1265.md"])]) == []

    def test_a_commit_that_names_every_item_it_writes_is_no_offence(self) -> None:
        found = records.offences([("Items 1264 and 1265: one change", ["changes/1264.md", "changes/1265.md"])])
        assert found == []

    def test_a_commit_that_names_no_item_is_no_offence(self) -> None:
        assert records.offences([("Merge branch main", ["changes/1264.md"])]) == []

    def test_a_file_that_is_no_record_is_no_offence(self) -> None:
        for path in ["changes/1265/notes.md", "docs/changes/1265.md", "changes/1265.md.bak"]:
            assert records.offences([("Item 1265: a phone's home", [path])]) == []


def test_a_commit_that_writes_another_items_records_is_an_offence() -> None:
    found = records.offences([("Item 1265: a phone's home", ["changes/1264.md", "roadmap/1264.md"])])
    assert len(found) == 2
    assert "changes/1264.md" in found[0]


def test_reads_a_repository_it_is_given(tmp_path: Path) -> None:
    # A real history: main with item 1, then a commit of item 2 that writes item 1's record.
    environment = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}

    def run(*arguments: str) -> None:
        subprocess.run(["git", *arguments], cwd=tmp_path, check=True, capture_output=True, env=environment)

    run("init", "-q", "-b", "main")
    run("config", "user.email", "dot@example.com")
    run("config", "user.name", "Dot")
    (tmp_path / "changes").mkdir()
    (tmp_path / "changes" / "1.md").write_text("item 1\n")
    run("add", "-A")
    run("commit", "-q", "-m", "Item 1: the first")
    (tmp_path / "changes" / "1.md").write_text("item 2 was here\n")
    (tmp_path / "changes" / "2.md").write_text("item 2\n")
    run("add", "-A")
    run("commit", "-q", "-m", "Item 2: the second")

    found = records.offences(records.commits("main~1", "HEAD", cwd=str(tmp_path)))
    assert len(found) == 1
    assert "changes/1.md" in found[0]
