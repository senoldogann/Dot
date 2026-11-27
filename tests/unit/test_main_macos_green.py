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


def _listed(run_id: int, created: str, status: str, conclusion: str | None) -> dict[str, object]:
    return {"id": run_id, "created_at": created, "status": status, "conclusion": conclusion}


def test_the_browser_suite_speaks_by_its_newest_finished_run_that_was_not_cancelled() -> None:
    """Item 1936: a red night counts for main until a later whole-suite run passes; one still going (hours, every night)
    is not waited for, and one a newer run cancelled says nothing."""
    assert "browser.yml" in green.WORKFLOWS
    runs = [
        _listed(4, "2026-11-27T01:44:00Z", "in_progress", None),
        _listed(3, "2026-11-26T20:00:00Z", "completed", "cancelled"),
        _listed(2, "2026-11-26T01:44:00Z", "completed", "failure"),
        _listed(1, "2026-11-25T01:44:00Z", "completed", "success"),
    ]
    assert green.newest("browser.yml", runs, None) == runs[2]
    # The night's own report job reads its own run, still going.
    assert green.newest("browser.yml", runs, "4") == runs[0]
    assert green.newest("browser.yml", [runs[0], runs[1]], None) is None
    # The gate and ios.yml take their newest run, whatever its state.
    assert green.newest("gate.yml", runs, None) == runs[0]
    assert "head_sha=abc" in green.query("gate.yml", "abc")
    assert "event=push" in green.query("ios.yml", "abc")
    assert "head_sha" not in green.query("browser.yml", "abc") and "event" not in green.query("browser.yml", "abc")


def test_a_red_night_keeps_main_red_though_its_gate_passed() -> None:
    gate = _run("gate.yml", "completed", "success")
    assert green.verdict([gate, _run("browser.yml", "completed", "failure", ("browser",))]) == "red"
    assert green.verdict([gate, _run("browser.yml", "completed", "success")]) == "green"
