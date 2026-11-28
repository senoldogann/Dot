"""scripts/merge_pr.py (item 945): which files `main` changed under a pull request since its checks ran, and which run
of the gate counts. A pull request another actor opened is left alone; one this automation opened can still be
brought up to main and merged."""

import importlib.util
import json
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


def test_markdown_counts_when_a_test_names_it_and_the_index_never() -> None:
    assert not merge_pr.counts("roadmap/INDEX.md", True)
    assert not merge_pr.counts("docs/runs.md", False) and not merge_pr.counts("ios/Next/README.md", False)
    assert merge_pr.counts("docs/daemon.md", True)
    for always in ["roadmap/1324.md", "changes/1863.md", "src/dotagent/skills/builtin/captcha/SKILL.md"]:
        assert not merge_pr.asks_the_tests(always) and merge_pr.counts(always, False)
    for code in ["docs/schemas/thread-sync.schema.json", "src/dotagent/agent/loop.py"]:
        assert not merge_pr.asks_the_tests(code) and merge_pr.counts(code, False)


def test_the_newest_run_of_the_gate_counts_and_none_refuses_the_merge() -> None:
    newest = merge_pr.GateRun(run_id=2, created_at="2026-11-03T10:00:00Z", status="completed", conclusion="success")
    older = merge_pr.GateRun(run_id=1, created_at="2026-11-03T09:00:00Z", status="completed", conclusion="failure")
    assert merge_pr.latest_gate_run([newest, older], "abc") == newest
    with pytest.raises(merge_pr.MergeRefused, match="no run of the gate"):
        merge_pr.latest_gate_run([], "abc")


def test_commits_count_from_a_margin_before_the_run_was_made() -> None:
    assert merge_pr.counted_since("2026-11-03T10:00:00Z", timedelta(minutes=15)) == "2026-11-03T09:45:00+00:00"


def test_only_the_account_this_automation_acts_as_counts_as_its_own() -> None:
    assert merge_pr.opened_by_us("dot", "dot")
    assert merge_pr.opened_by_us("Dot", "dot")
    assert not merge_pr.opened_by_us("stranger", "dot")
    assert not merge_pr.opened_by_us("", "dot")
    assert not merge_pr.opened_by_us("dot", "")


def _install(
    monkeypatch: pytest.MonkeyPatch,
    answers: dict[tuple[str, ...], str],
    holds: bool,
    red: frozenset[int],
) -> list[list[str]]:
    """``_run`` answers ``answers`` (matched on a prefix of the command) and records every command; main is red on
    macOS by the runs ``red`` (none: green)."""
    calls: list[list[str]] = []

    def run(argv: list[str]) -> str:
        calls.append(list(argv))
        for prefix, body in answers.items():
            if tuple(argv[: len(prefix)]) == prefix:
                return body
        raise AssertionError(argv)

    monkeypatch.setattr(merge_pr, "_run", run)
    monkeypatch.setattr(merge_pr, "_holds", lambda head, commit: holds)
    monkeypatch.setattr(merge_pr, "_main_red", lambda: red)
    return calls


_OURS: str = json.dumps(
    {"state": "OPEN", "headRefOid": "abc123", "author": {"login": "dot"}, "title": "docs: dependency audit notes"}
)
_GATE: str = json.dumps(
    [{"databaseId": 1, "createdAt": "2026-11-03T10:00:00Z", "status": "completed", "conclusion": "success"}]
)


def _answers(view: str, moved: str) -> dict[tuple[str, ...], str]:
    return {
        ("gh", "pr", "view"): view,
        ("gh", "api", "user"): "dot\n",
        ("gh", "api", "--paginate"): "src/dotagent/agent/loop.py\n",
        ("gh", "run", "list"): _GATE,
        ("git", "fetch"): "",
        ("git", "log"): moved,
        ("gh", "pr", "update-branch"): "",
        ("gh", "pr", "merge"): "",
    }


def _touched(calls: list[list[str]]) -> list[list[str]]:
    """The commands that would change the pull request on GitHub."""
    return [argv for argv in calls if argv[1:3] in (["pr", "update-branch"], ["pr", "merge"])]


def test_a_docs_pull_request_opened_by_someone_else_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """PR #888 looked like docs and was not this automation's: no update of its branch and no merge."""
    view: str = json.dumps(
        {
            "state": "OPEN",
            "headRefOid": "abc123",
            "author": {"login": "stranger"},
            "title": "docs: dependency audit notes",
        }
    )
    calls: list[list[str]] = _install(monkeypatch, _answers(view, "src/dotagent/agent/loop.py\n"), False, frozenset())
    with pytest.raises(merge_pr.MergeRefused, match="left alone"):
        merge_pr.merge(888, "squash")
    assert _touched(calls) == []


def test_a_pull_request_this_automation_opened_is_brought_up_to_main(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = _install(monkeypatch, _answers(_OURS, "src/dotagent/agent/loop.py\n"), False, frozenset())
    assert merge_pr.merge(7, "rebase") == 2
    assert _touched(calls) == [["gh", "pr", "update-branch", "7"]]


def test_a_pull_request_this_automation_opened_is_merged_when_main_did_not_touch_its_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = _install(monkeypatch, _answers(_OURS, "docs/other.md\n"), False, frozenset())
    assert merge_pr.merge(7, "squash") == 0
    assert _touched(calls) == [
        ["gh", "pr", "merge", "7", "--squash", "--delete-branch", "--match-head-commit", "abc123"]
    ]


def test_a_fix_names_the_red_runs_by_id_or_by_link_on_its_own_line() -> None:
    body: str = (
        "Fixes the phone tests.\n\nFixes-main-red: 19876543210\n"
        "Fixes-main-red: https://github.com/o/r/actions/runs/19876543299/job/55512345678\n"
        "Not a line of its own: Fixes-main-red: 19876543111\n"
    )
    assert merge_pr.declared_fixes(body) == frozenset({19876543210, 19876543299})
    assert merge_pr.declared_fixes("item 2015, run 19876543210 was red") == frozenset()


def test_a_red_main_refuses_all_but_a_fix_of_one_of_its_runs() -> None:
    red: frozenset[int] = frozenset({19876543210, 19876543299})
    assert merge_pr.red_main_refusal(frozenset(), frozenset()) is None
    assert merge_pr.red_main_refusal(red, frozenset({19876543299})) is None
    refused: str | None = merge_pr.red_main_refusal(red, frozenset())
    assert refused is not None and "19876543210, 19876543299" in refused and "Fixes-main-red" in refused
    stale: str | None = merge_pr.red_main_refusal(red, frozenset({11111111111}))
    assert stale is not None and "11111111111, which is no red run now" in stale


def test_main_red_on_macos_stops_the_merge_and_the_update_of_the_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    """2026-11-28: pull requests were merged while main's macOS run was red; the rule was prose alone."""
    calls: list[list[str]] = _install(monkeypatch, _answers(_OURS, "docs/other.md\n"), False, frozenset({19876543210}))
    with pytest.raises(merge_pr.MergeRefused, match="main is red on macOS"):
        merge_pr.merge(7, "squash")
    assert _touched(calls) == []


def test_the_pull_request_that_fixes_the_red_is_merged(monkeypatch: pytest.MonkeyPatch) -> None:
    view: str = json.dumps({**json.loads(_OURS), "body": "Fixes the gate.\n\nFixes-main-red: 19876543210\n"})
    calls: list[list[str]] = _install(monkeypatch, _answers(view, "docs/other.md\n"), False, frozenset({19876543210}))
    assert merge_pr.merge(7, "squash") == 0
    assert _touched(calls) == [
        ["gh", "pr", "merge", "7", "--squash", "--delete-branch", "--match-head-commit", "abc123"]
    ]


def test_a_main_whose_state_cannot_be_read_is_not_merged_over(monkeypatch: pytest.MonkeyPatch) -> None:
    def unread() -> frozenset[int]:
        raise RuntimeError("gh api ... failed (1): HTTP 502")

    monkeypatch.setattr(merge_pr.main_macos_green, "red_now", unread)
    with pytest.raises(merge_pr.MergeRefused, match="cannot be read: gh api"):
        merge_pr._main_red()
