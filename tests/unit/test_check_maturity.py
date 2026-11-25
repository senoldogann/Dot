"""The maturity scorecard's checks (item 1857): the gate's (scripts/check_maturity.py) reads the table, refuses a row
that is not one, a feature named twice and a date ahead of today, and never fails on age; the daily one
(scripts/maturity_lapsed.py) lists the rows proven more than the limit ago as an issue's body, an unproven row never."""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPTS: Path = Path(__file__).resolve().parents[2] / "scripts"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    # maturity_lapsed imports check_maturity by name, as it does when run from scripts/.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


maturity = _load("check_maturity")
stale = _load("maturity_lapsed")


def _card(*rows: str) -> str:
    head: list[str] = ["# Maturity", "<!-- maturity:start -->", "| Feature | Last proven | How | Known gaps |"]
    return "\n".join([*head, "| --- | --- | --- | --- |", *rows, "<!-- maturity:end -->"])


_ROWS: tuple[str, ...] = (
    "| Schedules | 2026-11-25 | 83 of 92 runs done | |",
    "| Briefs | 2026-10-25 | a firing done | off |",
    "| Meet | unproven | both tries failed | never live |",
)


def test_a_row_past_the_limit_lapses_and_an_unproven_one_never_does() -> None:
    rows = maturity.read_rows(_card(*_ROWS))
    assert [row.feature for row in maturity.lapsed(rows, date(2026, 11, 25), 30)] == ["Briefs"]
    assert maturity.lapsed(rows, date(2026, 11, 24), 30) == []
    assert maturity.from_future(rows, date(2026, 11, 24))[0].feature == "Schedules"
    body: str = stale.issue_body(maturity.lapsed(rows, date(2026, 11, 25), 30), date(2026, 11, 25), 30)
    assert "- **Briefs**: last proven 2026-10-25 (a firing done)" in body
    assert "Meet" not in body and "Schedules" not in body


@pytest.mark.parametrize(
    "rows",
    [
        ("| Schedules | last week | runs | |",),
        ("| Schedules | 2026-11-25 | | |",),
        ("| Schedules | 2026-11-25 | runs |",),
        ("| Schedules | 2026-02-30 | runs | |",),
        ("| Schedules | 2026-11-25 | runs | |", "| Schedules | unproven | none | |"),
    ],
)
def test_a_table_that_is_not_one_says_so(rows: tuple[str, ...]) -> None:
    with pytest.raises(ValueError, match=r"row|twice"):
        maturity.read_rows(_card(*rows))


def test_the_checked_in_scorecard_reads_and_holds_its_unproven_rows() -> None:
    rows = maturity.read_rows(maturity.SCORECARD.read_text(encoding="utf-8"))
    unproven: set[str] = {row.feature for row in rows if row.proven is None}
    assert "Meetings (Google Meet)" in unproven


def test_the_daily_check_exits_2_on_a_table_it_cannot_read_and_1_on_stale_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    card: Path = tmp_path / "maturity.md"
    monkeypatch.setattr(stale, "SCORECARD", card)
    monkeypatch.setattr(sys, "argv", ["maturity_lapsed.py", "--today", "2026-11-25"])
    card.write_text(_card("| Schedules | last week | runs | |"), encoding="utf-8")
    assert stale.main() == 2
    assert "maturity.md" in capsys.readouterr().err
    card.write_text(_card(*_ROWS), encoding="utf-8")
    assert stale.main() == 1
    card.write_text(_card(_ROWS[0]), encoding="utf-8")
    assert stale.main() == 0
