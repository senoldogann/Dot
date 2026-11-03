"""scripts/merge_pr.py (item 945): which files `main` changed under a pull request since its checks ran, and which run
of the gate counts."""

import importlib.util
from datetime import timedelta
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT: Path = Path(__file__).resolve().parents[2] / "scripts" / "merge_pr.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("merge_pr", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


merge_pr = _load()


def test_only_the_files_both_changed_send_the_branch_back_to_main() -> None:
    changed: list[str] = ["src/dotagent/agent/loop.py", "roadmap/945.md", "tests/unit/test_loop.py"]
    assert merge_pr.shared_files(changed, []) == []
    assert merge_pr.shared_files(changed, ["roadmap/944.md", "src/dotagent/server/app.py"]) == []
    moved: list[str] = ["tests/unit/test_loop.py", "src/dotagent/agent/loop.py", "src/dotagent/agent/loop.py"]
    assert merge_pr.shared_files(changed, moved) == ["src/dotagent/agent/loop.py", "tests/unit/test_loop.py"]


def test_the_newest_run_of_the_gate_counts_and_none_refuses_the_merge() -> None:
    newest = merge_pr.GateRun(run_id=2, created_at="2026-11-03T10:00:00Z", status="completed", conclusion="success")
    older = merge_pr.GateRun(run_id=1, created_at="2026-11-03T09:00:00Z", status="completed", conclusion="failure")
    assert merge_pr.latest_gate_run([newest, older], "abc") == newest
    with pytest.raises(merge_pr.MergeRefused, match="no run of the gate"):
        merge_pr.latest_gate_run([], "abc")


def test_commits_count_from_a_margin_before_the_run_was_made() -> None:
    assert merge_pr.counted_since("2026-11-03T10:00:00Z", timedelta(minutes=15)) == "2026-11-03T09:45:00+00:00"


def test_a_pull_request_of_prose_alone_has_no_gate_to_wait_for() -> None:
    ignored: list[str] = ["*.md", "docs/**", ".github/ISSUE_TEMPLATE/**", ".github/pull_request_template.md"]
    assert not merge_pr.gate_runs_for(["README.md", "docs/repair.md", ".github/ISSUE_TEMPLATE/bug.md"], ignored)
    # A roadmap entry is not at the root, and code runs it whatever else is prose.
    assert merge_pr.gate_runs_for(["roadmap/945.md"], ignored)
    assert merge_pr.gate_runs_for(["docs/repair.md", "src/dotagent/repair/night.py"], ignored)
