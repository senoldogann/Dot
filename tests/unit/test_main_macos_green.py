"""scripts/main_macos_green.py: the verdict on main's newest commit from its macOS runs, and the issue's words."""

import importlib.util
from pathlib import Path
from types import ModuleType

_SCRIPT: Path = Path(__file__).resolve().parents[2] / "scripts" / "main_macos_green.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("main_macos_green", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


green = _load()


def _run(workflow: str, status: str, conclusion: str, failed: tuple[str, ...] = ()) -> object:
    return green.Run(
        workflow=workflow, status=status, conclusion=conclusion, url=f"https://x/{workflow}", failed_jobs=failed
    )


def test_a_finished_gate_that_passed_is_green_and_one_that_failed_is_red() -> None:
    assert green.verdict([_run("gate.yml", "completed", "success")]) == "green"
    assert green.verdict([_run("gate.yml", "completed", "success"), _run("ios.yml", "completed", "success")]) == "green"
    assert green.verdict([_run("gate.yml", "completed", "failure", ("browser (pages)",))]) == "red"
    assert green.verdict([_run("gate.yml", "completed", "success"), _run("ios.yml", "completed", "cancelled")]) == "red"


def test_no_gate_run_yet_or_one_still_going_is_pending_but_a_failure_already_is_red() -> None:
    assert green.verdict([]) == "pending"
    assert green.verdict([_run("ios.yml", "completed", "success")]) == "pending"
    assert green.verdict([_run("gate.yml", "in_progress", "")]) == "pending"
    assert green.verdict([_run("gate.yml", "completed", "success"), _run("ios.yml", "queued", "")]) == "pending"
    assert green.verdict([_run("gate.yml", "in_progress", ""), _run("ios.yml", "completed", "failure")]) == "red"


def test_the_issue_names_each_failed_run_and_its_jobs() -> None:
    body: str = green.report_body(
        "abc123",
        [
            _run("gate.yml", "completed", "failure", ("gate", "browser (phone 2)")),
            _run("ios.yml", "completed", "success"),
        ],
    )
    assert "abc123" in body
    assert "gate.yml: failure (gate, browser (phone 2)) https://x/gate.yml" in body
    assert "ios.yml" not in body


def test_the_report_reads_its_own_unfinished_run_from_the_other_jobs() -> None:
    done = {"status": "completed", "conclusion": "success"}
    jobs = [
        {"name": "gate", **done},
        {"name": "browser (pages)", **done},
        {"name": "main-health", "status": "in_progress", "conclusion": None},
    ]
    assert green.this_run("gate.yml", "u", jobs).conclusion == "success"
    assert green.verdict([green.this_run("gate.yml", "u", jobs)]) == "green"
    jobs[1] = {"name": "browser (pages)", "status": "completed", "conclusion": "failure"}
    run = green.this_run("gate.yml", "u", jobs)
    assert (run.conclusion, run.failed_jobs) == ("failure", ("browser (pages)",))
    assert green.verdict([run]) == "red"
