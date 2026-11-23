"""The window a goal check may open on the work (item 1363): it may ask for the lines of a file the run wrote, is asked
again with them once, and never reads a file the run did not write."""

import asyncio
import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from dotagent.agent.goal_read import READ_LINES, GoalRead, read_written
from dotagent.agent.loop import _checked
from dotagent.agent.review import GoalRequest, GoalVerdict, _goal_checked, _goal_prompt
from dotagent.agent.verify import RunFacts
from dotagent.core.errors import ProviderProtocolError

_REQUEST = GoalRequest(
    goal="write the report",
    plan="",
    targets="",
    digest="",
    facts="",
    answer="written",
    claimed_success=True,
    keep_going=False,
)


def _facts(*written: Path) -> RunFacts:
    return RunFacts(written=tuple(str(path) for path in written), failures=(), resolved=(), checks=(), named=())


def test_the_check_reads_the_lines_it_named_of_a_file_the_run_wrote(tmp_path: Path) -> None:
    report: Path = tmp_path / "report.md"
    report.write_text("\n".join(f"line {number}" for number in range(1, 21)), encoding="utf-8")

    shown: str = read_written([GoalRead(path=str(report), from_line=3, lines=2)], (str(report),), tmp_path)

    assert "lines 3-4 of 20" in shown
    assert "line 3" in shown and "line 4" in shown and "line 5" not in shown


def test_a_file_the_run_did_not_write_is_never_read(tmp_path: Path) -> None:
    secret: Path = tmp_path / "notes.txt"
    secret.write_text("do not show this", encoding="utf-8")

    shown: str = read_written([GoalRead(path=str(secret))], (), tmp_path)

    assert "not a file this run wrote" in shown
    assert "do not show this" not in shown


def test_a_file_too_large_to_be_the_work_is_not_read(tmp_path: Path) -> None:
    huge: Path = tmp_path / "huge.bin"
    huge.write_bytes(b"x" * 2_000_001)

    shown: str = read_written([GoalRead(path=str(huge))], (str(huge),), tmp_path)

    assert "too large to read here" in shown


def test_a_secret_longer_than_a_line_share_is_masked_before_the_line_is_cut(tmp_path: Path) -> None:
    report: Path = tmp_path / "report.md"
    report.write_text(f"api_key = sk-{'a' * 400}\n", encoding="utf-8")

    shown: str = read_written([GoalRead(path=str(report))], (str(report),), tmp_path)

    assert "aaaa" not in shown and "***" in shown


def test_a_file_swapped_for_a_link_is_not_read(tmp_path: Path) -> None:
    outside: Path = tmp_path / "outside"
    outside.mkdir()
    secret: Path = outside / "id_rsa"
    secret.write_text("PRIVATE KEY", encoding="utf-8")
    report: Path = tmp_path / "report.md"
    report.write_text("the report", encoding="utf-8")
    report.unlink()
    report.symlink_to(secret)

    shown: str = read_written([GoalRead(path=str(report))], (str(report),), tmp_path)

    assert "PRIVATE KEY" not in shown
    assert "could not be read" in shown


def test_a_hard_link_is_not_read(tmp_path: Path) -> None:
    secret: Path = tmp_path / "id_rsa"
    secret.write_text("PRIVATE KEY", encoding="utf-8")
    report: Path = tmp_path / "report.md"
    os.link(secret, report)

    shown: str = read_written([GoalRead(path=str(report))], (str(report),), tmp_path)

    assert "PRIVATE KEY" not in shown
    assert "could not be read" in shown


def test_a_folder_swapped_for_a_link_is_not_read(tmp_path: Path) -> None:
    outside: Path = tmp_path / "outside"
    outside.mkdir()
    (outside / "report.md").write_text("PRIVATE KEY", encoding="utf-8")
    folder: Path = tmp_path / "work"
    folder.mkdir()
    report: Path = folder / "report.md"
    report.write_text("the report", encoding="utf-8")
    shutil.move(str(folder), str(tmp_path / "work-away"))
    folder.symlink_to(outside)

    shown: str = read_written([GoalRead(path=str(report))], (str(report),), tmp_path)

    assert "PRIVATE KEY" not in shown
    assert "could not be read" in shown


def test_the_window_shows_no_more_than_its_lines_and_masks_a_secret(tmp_path: Path) -> None:
    report: Path = tmp_path / "report.md"
    report.write_text(
        "\n".join(["api_key = sk-1234567890abcdef", *[f"line {number}" for number in range(2, READ_LINES + 10)]]),
        encoding="utf-8",
    )

    shown: str = read_written([GoalRead(path=str(report), lines=READ_LINES)], (str(report),), tmp_path)

    assert "sk-1234567890abcdef" not in shown and "***" in shown
    assert f"line {READ_LINES}" in shown and f"line {READ_LINES + 1}" not in shown


def test_inspect_names_the_files_to_read_and_no_decision_decides_them() -> None:
    inspect = GoalVerdict(decision="inspect", reason="need it", unproven_steps=(), reads=(GoalRead(path="/w/a.md"),))
    assert _goal_checked(inspect, inspected=False) == inspect
    with pytest.raises(ProviderProtocolError):
        _goal_checked(inspect, inspected=True)
    with pytest.raises(ProviderProtocolError):
        _goal_checked(inspect, inspected=False, inspectable=False)
    with pytest.raises(ProviderProtocolError):
        _goal_checked(GoalVerdict(decision="inspect", reason="need it", unproven_steps=()), inspected=False)
    with pytest.raises(ProviderProtocolError):
        _goal_checked(
            GoalVerdict(
                decision="achieved",
                reason="done",
                unproven_steps=(),
                reads=(GoalRead(path="/w/a.md"),),
            ),
            inspected=False,
        )


def test_a_check_that_cannot_read_is_told_so() -> None:
    prompt: str = _goal_prompt(_REQUEST.model_copy(update={"inspectable": False}), "nonce")
    assert "inspect is not available in this check" in prompt
    assert "inspect is not available" not in _goal_prompt(_REQUEST, "nonce")


def test_the_check_is_told_what_it_asked_to_read_and_that_it_must_decide_now() -> None:
    asked: GoalRequest = _REQUEST.model_copy(update={"evidence": "/w/a.md (lines 1-2 of 9):\nline 1\nline 2"})
    prompt: str = _goal_prompt(asked, "nonce")
    assert "WHAT YOU ASKED TO READ" in prompt
    assert "line 2" in prompt
    assert "You have inspected once: decide now" in prompt
    assert "WHAT YOU ASKED TO READ" not in _goal_prompt(_REQUEST, "nonce")


def test_a_check_that_asks_to_read_the_work_is_asked_again_with_what_it_holds(tmp_path: Path) -> None:
    report: Path = tmp_path / "report.md"
    report.write_text("first line\nsecond line\n", encoding="utf-8")
    facts: RunFacts = _facts(report)
    asked: list[GoalRequest] = []
    verdicts: list[GoalVerdict] = [
        GoalVerdict(decision="inspect", reason="need it", unproven_steps=(), reads=(GoalRead(path=str(report)),)),
        GoalVerdict(decision="achieved", reason="it holds", unproven_steps=()),
    ]

    async def check(request: GoalRequest) -> GoalVerdict:
        asked.append(request)
        return verdicts.pop(0)

    agent = SimpleNamespace(runtime=SimpleNamespace(workspace=tmp_path))
    verdict: GoalVerdict = asyncio.run(_checked(agent, check, _REQUEST, facts))  # type: ignore[arg-type]

    assert verdict.decision == "achieved"
    assert [request.evidence for request in asked] == ["", f"{report} (lines 1-2 of 2):\nfirst line\nsecond line"]


def test_a_check_that_decides_is_asked_once(tmp_path: Path) -> None:
    asked: list[GoalRequest] = []

    async def check(request: GoalRequest) -> GoalVerdict:
        asked.append(request)
        return GoalVerdict(decision="continue", reason="not yet", unproven_steps=())

    agent = SimpleNamespace(runtime=SimpleNamespace(workspace=tmp_path))
    verdict: GoalVerdict = asyncio.run(_checked(agent, check, _REQUEST, _facts()))  # type: ignore[arg-type]

    assert verdict.decision == "continue"
    assert len(asked) == 1
