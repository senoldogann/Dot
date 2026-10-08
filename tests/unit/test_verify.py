"""Checking the end of a run: the facts the harness keeps from the tool calls, the paths a final answer names and which
of them are missing, the facts as the goal check reads them, and the doctor row of the review model."""

from pathlib import Path

import pytest

from dotagent.agent.executor import ToolOutcome
from dotagent.agent.verify import (
    CallFact,
    NamedPath,
    RunFacts,
    answer_paths,
    missing_paths,
    new_facts,
    render_facts,
    unopened_addresses,
    with_outcomes,
)
from dotagent.config import ModelsConfig, load_default_config
from dotagent.core.types import JsonObject
from dotagent.doctor import CheckResult, _review_row
from dotagent.llm.types import ToolCall
from dotagent.policy.types import ActionInfo, ActionKind
from dotagent.tools.base import ToolResult, fail, ok

PYTEST_FAILED: str = "[exit 1 in 0.4s]\nF...\n=== FAILURES ===\nE   assert 1 == 2\n\n1 failed, 3 passed in 0.12s"
PYTEST_PASSED: str = "[exit 0 in 0.3s]\n....\n\n4 passed in 0.10s\n"


def _call(name: str, arguments: JsonObject) -> ToolCall:
    return ToolCall(id=name, name=name, arguments=arguments)


def _ran(name: str, arguments: JsonObject, kind: ActionKind, target: str, result: ToolResult) -> ToolOutcome:
    action: ActionInfo = ActionInfo(tool=name, kind=kind, risk="safe", summary=name, target=target, flags=())
    return ToolOutcome(call=_call(name, arguments), result=result, seconds=0.0, action=action)


def _refused(name: str, arguments: JsonObject, result: ToolResult) -> ToolOutcome:
    """A call that never ran: an unknown tool, or arguments that did not fit it."""
    return ToolOutcome(call=_call(name, arguments), result=result, seconds=0.0, action=None)


def _shell(command: str, result: ToolResult) -> ToolOutcome:
    return _ran("shell", {"command": command}, "shell", command, result)


def test_the_facts_keep_written_files_unresolved_failures_and_the_newest_result_of_each_check() -> None:
    facts: RunFacts = with_outcomes(
        new_facts(),
        [
            _ran(
                "write_file", {"path": "report.md", "content": "See /tmp/data.csv"}, "write", "/w/report.md", ok("ok")
            ),
            _ran("read_file", {"path": "notes.txt"}, "read", "/w/notes.txt", fail("file does not exist\n(notes.txt)")),
            _shell("pytest -q", fail(PYTEST_FAILED)),
            _refused("browser_open", {"url": "https://example.org"}, fail("Unknown tool 'browser_open'")),
            _ran("update_plan", {"steps": []}, "meta", "", fail("a plan needs a step")),
        ],
    )
    assert facts.failures == (
        CallFact(tool="read_file", target="/w/notes.txt", ok=False, result="file does not exist"),
        CallFact(tool="shell", target="pytest -q", ok=False, result="[exit 1 in 0.4s]"),
    )
    assert facts.checks == (
        CallFact(tool="shell", target="pytest -q", ok=False, result="[exit 1 in 0.4s] … 1 failed, 3 passed in 0.12s"),
    )
    later: RunFacts = with_outcomes(
        facts,
        [
            _shell("pytest -q", ok(PYTEST_PASSED)),
            _shell("ls /tmp/out", ok("[exit 0 in 0.0s]\na.txt")),
            _ran("web_fetch", {"url": "https://example.org/a"}, "network", "https://example.org/a", ok("text")),
        ],
    )
    # A success of the same tool and target resolves a failure; a check that runs again replaces its entry.
    assert later.failures == facts.failures[:1]
    assert later.checks == (
        CallFact(tool="shell", target="pytest -q", ok=True, result="[exit 0 in 0.3s] … 4 passed in 0.10s"),
    )
    assert later.written == ("/w/report.md",)
    # What successful calls named: the paths and addresses in their arguments, and the file a file tool acted on.
    assert later.named == ("/tmp/data.csv", "/w/report.md", "/tmp/out", "https://example.org/a")


def test_the_facts_keep_the_newest_failures_and_each_written_file_once() -> None:
    reads: list[ToolOutcome] = [
        _ran("read_file", {"path": f"{index}.txt"}, "read", f"/w/{index}.txt", fail("file does not exist"))
        for index in range(12)
    ]
    writes: list[ToolOutcome] = [
        _ran("write_file", {"path": name, "content": ""}, "write", f"/w/{name}", ok("ok"))
        for name in ("a.md", "b.md", "a.md")
    ]
    facts: RunFacts = with_outcomes(new_facts(), [*reads, *writes])
    assert [fact.target for fact in facts.failures] == [f"/w/{index}.txt" for index in range(2, 12)]
    assert facts.written == ("/w/b.md", "/w/a.md")


@pytest.mark.parametrize(
    ("command", "check"),
    [
        ("pytest -q tests", True),
        ("cd app && .venv/bin/ruff check src", True),
        ("python -m unittest discover", True),
        ("npm run test:unit", True),
        ("pnpm build", True),
        ("cargo clippy --all-targets", True),
        ("go vet ./...", True),
        ("./gradlew build", True),
        ("xcodebuild -scheme App test", True),
        ("cat pytest.ini", False),
        ("npm install", False),
        ("go run main.go", False),
        ("git commit -m 'make it build'", False),
    ],
)
def test_a_check_is_a_command_that_tests_builds_or_lints(command: str, check: bool) -> None:
    facts: RunFacts = with_outcomes(new_facts(), [_shell(command, ok("[exit 0 in 0.1s]"))])
    assert (facts.checks != ()) == check


def test_a_path_in_an_answer_is_also_read_on_past_its_spaces() -> None:
    answer: str = (
        "Saved to ~/Library/Application Support/Dot/notes.md and /tmp/out.txt, see https://example.org/a and run "
        "run_abc123_def4."
    )
    assert answer_paths(answer) == (
        NamedPath(name="/tmp/out.txt", readings=("/tmp/out.txt",)),
        NamedPath(
            name="~/Library/Application",
            readings=(
                "~/Library/Application",
                "~/Library/Application Support/Dot/notes.md",
                "~/Library/Application Support/Dot/notes.md and",
            ),
        ),
    )


def test_a_path_is_missing_when_no_reading_exists_and_no_successful_call_named_it(tmp_path: Path) -> None:
    (tmp_path / "Application Support").mkdir()
    (tmp_path / "Application Support" / "notes.md").write_text("notes")
    (tmp_path / "real.txt").write_text("real")
    answer: str = (
        f"See {tmp_path}/Application Support/notes.md and {tmp_path}/real.txt; {tmp_path}/gone.txt is new, "
        f"{tmp_path}/moved.txt was moved away and {tmp_path}/made.txt was made."
    )
    # A relative value is a path in the workspace.
    named: tuple[str, ...] = ("moved.txt", f"{tmp_path}/made.txt", "https://example.org")
    assert missing_paths(answer_paths(answer), named, tmp_path) == (f"{tmp_path}/gone.txt",)


def test_the_goal_check_reads_every_section_of_the_facts() -> None:
    facts: RunFacts = RunFacts(
        written=("/w/report.md",),
        failures=(CallFact(tool="read_file", target="/w/notes.txt", ok=False, result="file does not exist"),),
        checks=(CallFact(tool="shell", target="pytest -q", ok=True, result="[exit 0 in 0.3s] … 4 passed in 0.10s"),),
        named=("/w/report.md", "https://example.org/a"),
    )
    unopened: tuple[str, ...] = unopened_addresses(
        "Read https://example.org/a/ and https://example.com/b.", facts.named
    )
    assert unopened == ("https://example.com/b",)
    assert render_facts(facts, ("/w/data.csv",), unopened) == (
        "Files the file tools wrote or edited:\n- /w/report.md\n\n"
        "Failed calls that no later success of the same tool and target followed:\n"
        "- read_file /w/notes.txt: file does not exist\n\n"
        "Commands that test, build or lint, with their results:\n"
        "- pytest -q: succeeded, [exit 0 in 0.3s] … 4 passed in 0.10s\n\n"
        "Paths the final answer names that do not exist and that no successful call named:\n- /w/data.csv\n\n"
        "Addresses the final answer names that no call of the run opened:\n- https://example.com/b"
    )
    assert render_facts(new_facts(), (), ()).count(":\nnone") == 5


def test_the_doctor_warns_when_the_main_model_is_its_own_review_model() -> None:
    models: ModelsConfig = load_default_config().models.model_copy(
        update={"main": "ollama:minimax-m2.7:cloud", "review": "ollama:minimax-m2.7:cloud"}
    )
    same: CheckResult = _review_row(models)
    assert (same.name, same.ok, same.required) == ("review model", False, False)
    assert same.detail.startswith("ollama:minimax-m2.7:cloud is also the main model")
    other: CheckResult = _review_row(models.model_copy(update={"review": "openai:gpt-5.4"}))
    assert (other.ok, other.required) == (True, False)
