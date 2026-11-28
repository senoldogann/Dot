"""scripts/weakened_tests.py: a test a branch weakens is named with why in a `Test-change:` line, and the pre-push
review is told which tests the change edits. On 2026-11-28 main went red because pull requests changed a test's words
while others changed the code, and fixes said "the test was wrong" with nothing but their word."""

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

_SCRIPT: Path = Path(__file__).resolve().parents[2] / "scripts" / "weakened_tests.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("weakened_tests", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


weakened = _load()


def _diff(path: str, removed: list[str], added: list[str]) -> str:
    return (
        f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -1,{len(removed)} +1,{len(added)} @@\n"
        + "".join(f"-{line}\n" for line in removed)
        + "".join(f"+{line}\n" for line in added)
    )


def _kinds(text: str) -> list[str]:
    return [weak.kind for weak in weakened.weakenings(weakened.parse_diff(text))]


def test_a_removed_or_changed_assertion_is_a_weakening_and_a_moved_or_reflowed_one_is_not() -> None:
    assert _kinds(_diff("tests/unit/test_a.py", ["    assert count == 2"], ["    assert count == 3"])) == [
        "assertion removed or changed"
    ]
    assert _kinds(_diff("tests/unit/test_a.py", ["    assert count == 2", "    x = 1"], ["    x = 2"])) == [
        "assertion removed or changed"
    ]
    moved: str = _diff("tests/unit/test_a.py", ["    assert count == 2"], []) + _diff(
        "tests/unit/test_b.py", [], ["        assert count==2"]
    )
    assert _kinds(moved) == []
    for line in ["await expect(page.locator('#x')).toBeVisible();", "XCTAssertEqual(a, b)", "#expect(a == b)"]:
        assert _kinds(_diff("tests/js/x.test.js", [line], [])) == ["assertion removed or changed"]
    assert _kinds(_diff("src/dotagent/x.py", ["    assert ready"], [])) == []


def test_a_removed_test_and_an_added_skip_xfail_rerun_or_timeout_mark_are_weakenings() -> None:
    assert _kinds(_diff("tests/unit/test_a.py", ["def test_it_counts() -> None:", "    pass"], [])) == ["test removed"]
    renamed: str = _diff("tests/unit/test_a.py", ["def test_it_counts() -> None:"], ["def test_it_counts() -> None:"])
    assert _kinds(renamed) == []
    for mark, kind in [
        ('@pytest.mark.skip(reason="flaky")', "skip or xfail added"),
        ("@pytest.mark.xfail", "skip or xfail added"),
        ("    pytest.skip('slow')", "skip or xfail added"),
        ("test.skip('opens the sheet', async () => {", "skip or xfail added"),
        ("        try XCTSkipIf(true)", "skip or xfail added"),
        ("@pytest.mark.flaky(reruns=3)", "rerun added"),
        ("@pytest.mark.timeout(600)", "timeout added"),
    ]:
        assert _kinds(_diff("tests/unit/test_a.py", [], [mark])) == [kind], mark
    assert _kinds(_diff("ios/DotTests/ChatStateTests.swift", [], ["        try XCTSkipIf(true)"])) == [
        "skip or xfail added"
    ]


def _new_file(path: str, added: list[str]) -> str:
    return (
        f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n+++ b/{path}\n@@ -0,0 +1,{len(added)} @@\n"
        + "".join(f"+{line}\n" for line in added)
    )


def test_a_mark_that_guards_a_new_test_or_moved_with_its_test_or_written_as_data_is_not_a_weakening() -> None:
    """The review of item 2100 measured 31 % of main's last 120 commits flagged, the new tests' guards among them."""
    assert _kinds(_new_file("tests/smoke/test_live.py", ["pytestmark = pytest.mark.skipif(True, reason='mac')"])) == []
    assert _kinds(_new_file("ios/DotUITests/BotsLiveUITests.swift", ['        throw XCTSkip("run by e2e")'])) == []
    new_test: list[str] = ["@pytest.mark.timeout(30)", "def test_rg() -> None:", "    pytest.skip('no rg')"]
    assert _kinds(_diff("tests/unit/test_a.py", [], new_test)) == []
    moved: str = _diff("tests/unit/test_a.py", ["@pytest.mark.skipif(LINUX, reason='mac')"], []) + _diff(
        "tests/unit/test_b.py", [], ["@pytest.mark.skipif(LINUX, reason='mac')"]
    )
    assert _kinds(moved) == []
    data: list[str] = [
        """    ('@pytest.mark.skip(reason="flaky")', "skip or xfail added"),""",
        "    words = 'test.skip(' + name  # pytest.mark.xfail is named here, not used",
    ]
    assert _kinds(_diff("tests/unit/test_a.py", [], data)) == []


def test_an_assertion_wrapped_over_lines_in_its_hunk_is_a_reflow_and_one_changed_is_not() -> None:
    wrapped: list[str] = ["    assert compute(", "        alpha, beta", "    ) == expected_value"]
    assert _kinds(_diff("tests/unit/test_a.py", ["    assert compute(alpha, beta) == expected_value"], wrapped)) == []
    changed: list[str] = ["    assert compute(", "        alpha, beta", "    ) == expected_value_2"]
    assert _kinds(_diff("tests/unit/test_a.py", ["    assert compute(alpha, beta) == expected_value"], changed)) == [
        "assertion removed or changed"
    ]


def test_a_removed_swift_test_unittest_raise_or_test_class_is_seen() -> None:
    assert _kinds(_diff("ios/DotTests/ChatTests.swift", ["    @Test func sendsOnce() async {"], [])) == ["test removed"]
    assert _kinds(_diff("tests/unit/test_a.py", ["class TestCount:"], [])) == ["test removed"]
    assert _kinds(_diff("tests/unit/test_a.py", ["        with self.assertRaises(ValueError):"], [])) == [
        "assertion removed or changed"
    ]


def test_a_timeout_raised_in_its_hunk_is_a_weakening_and_one_lowered_or_new_is_not() -> None:
    assert _kinds(_diff("tests/smoke/test_a.py", ["    wait(timeout=5)"], ["    wait(timeout=30)"])) == [
        "timeout raised from 5 to 30"
    ]
    assert _kinds(_diff("tests/smoke/test_a.py", ["    wait(timeout=30)"], ["    wait(timeout=5)"])) == []
    assert _kinds(_diff("tests/smoke/test_a.py", [], ["    run(cmd, timeout=60)"])) == []
    assert _kinds(_diff("tests/js/a.test.js", ["  timeout: 5_000,"], ["  timeout: 20_000,"])) == [
        "timeout raised from 5000 to 20000"
    ]
    assert _kinds(_diff("tests/smoke/test_a.py", ["    wait(timeout=5)"], ["    wait(timeout_ms=5000)"])) == []


def test_a_test_change_line_explains_a_file_it_names_with_a_reason() -> None:
    path: str = "tests/unit/test_phone.py"
    found = weakened.weakenings(
        weakened.parse_diff(_diff(path, ["    assert helpers == 2"], ["    assert helpers == 3"]))
    )
    said = weakened.explanations(
        (
            "Item 2018: three helpers\n\nTest-change: tests/unit/test_phone.py: item 2018 starts a third helper, "
            "the old count of two was the old code's\n",
        )
    )
    assert weakened.unexplained(found, said) == ()
    assert weakened.unexplained(found, weakened.explanations(("Test-change: test_phone.py: updated\n",))) == found
    assert weakened.unexplained(found, weakened.explanations(("Test-change: the count changed with the code\n",))) == (
        found
    )
    assert weakened.explains("test_phone.py now counts the third helper item 2018 adds", path)
    assert not weakened.explains("my_test_phone.py now counts the third helper", path)
    assert not weakened.explains("tests/unit/test_phone.py.bak now counts the third helper", path)


def test_the_brief_lists_the_tests_a_change_edits_with_the_code_and_nothing_for_new_tests_alone() -> None:
    both: str = _diff("src/dotagent/x.py", ["a = 1"], ["a = 2"]) + _diff(
        "tests/unit/test_x.py", ["    assert a == 1", "    # one"], ["    assert a == 2", "    # two"]
    )
    said: str = weakened.brief(weakened.parse_diff(both), ("tests/unit/test_x.py: a is 2 since item 9",))
    assert "## Test changes" in said and "`tests/unit/test_x.py`" in said
    assert "assertion removed or changed: `assert a == 1`" in said
    assert "- tests/unit/test_x.py: a is 2 since item 9" in said
    new_only: str = _diff("src/dotagent/x.py", ["a = 1"], ["a = 2"]) + _diff("tests/unit/test_x.py", [], ["    x = 1"])
    assert weakened.brief(weakened.parse_diff(new_only), ()) == ""
    tests_alone: str = _diff("tests/unit/test_x.py", ["    # one"], ["    # two"])
    assert weakened.brief(weakened.parse_diff(tests_alone), ()) == ""
    weakened_alone: str = _diff("tests/unit/test_x.py", ["    assert a == 1"], [])
    assert "No commit of the change has a `Test-change:` line." in weakened.brief(
        weakened.parse_diff(weakened_alone), ()
    )


def test_a_diff_line_that_looks_like_a_header_inside_a_hunk_is_read_as_a_line() -> None:
    text: str = (
        "diff --git a/tests/unit/test_q.py b/tests/unit/test_q.py\n--- a/tests/unit/test_q.py\n"
        "+++ b/tests/unit/test_q.py\n@@ -1,2 +1,1 @@\n--- a/sql comment\n-    assert rows == 1\n++++ kept\n"
        "diff --git a/tests/unit/test_gone.py b/tests/unit/test_gone.py\ndeleted file mode 100644\n"
        "--- a/tests/unit/test_gone.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-    assert gone\n"
    )
    files = weakened.parse_diff(text)
    assert [diff.path for diff in files] == ["tests/unit/test_q.py", "tests/unit/test_gone.py"]
    assert files[0].hunks[0].removed == ("-- a/sql comment", "    assert rows == 1")
    assert files[0].hunks[0].added == ("+++ kept",)
    assert [weak.path for weak in weakened.weakenings(files)] == ["tests/unit/test_q.py", "tests/unit/test_gone.py"]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()


def test_check_fails_a_branch_that_weakens_a_test_until_a_commit_says_why(tmp_path: Path) -> None:
    repo: Path = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "tests").mkdir()
    (repo / "tests" / "test_count.py").write_text("def test_count() -> None:\n    assert 1 + 1 == 2\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "main")
    _git(repo, "checkout", "-q", "-b", "feature")
    (repo / "tests" / "test_count.py").write_text("def test_count() -> None:\n    pass\n")
    _git(repo, "commit", "-q", "-am", "Item 1: no count")

    def check() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(_SCRIPT), "check", "--base", "main", "--head", "feature"],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
        )

    failed = check()
    assert failed.returncode == 1, failed.stdout + failed.stderr
    assert "tests/test_count.py: assertion removed or changed: assert 1 + 1 == 2" in failed.stderr
    _git(
        repo,
        "commit",
        "-q",
        "--amend",
        "-m",
        "Item 1: no count\n\nTest-change: tests/test_count.py: the sum moved to test_sum, which asserts it",
    )
    passed = check()
    assert passed.returncode == 0, passed.stdout + passed.stderr
