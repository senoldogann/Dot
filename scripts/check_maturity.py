"""Check the shape of Dot's maturity scorecard (``docs/maturity.md``, item 1857), as the gate does: the table between
its markers reads, every row names its feature once and says how it was proven, and its date is ``YYYY-MM-DD`` or
``unproven`` and never after today. How old a proof is never fails here: a date passes with the calendar and no change
of code, and a gate that failed on it would hold every merge. ``scripts/maturity_lapsed.py`` checks that, every day, in
``.github/workflows/maturity.yml``.

Usage: uv run python scripts/check_maturity.py [--today YYYY-MM-DD]
"""

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path

LIMIT_DAYS: int = 30
SCORECARD: Path = Path(__file__).resolve().parent.parent / "docs" / "maturity.md"
UNPROVEN: str = "unproven"
_START: str = "<!-- maturity:start -->"
_END: str = "<!-- maturity:end -->"
_DATE: re.Pattern[str] = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass(frozen=True)
class Row:
    """A feature of the scorecard: its name, the date it was last proven (``None``: unproven) and how."""

    feature: str
    proven: date | None
    how: str


def read_rows(text: str) -> list[Row]:
    """The rows between the scorecard's markers; a ``ValueError`` that names the row for a table that is not one, a date
    that is not a day, and a feature named twice."""
    if text.count(_START) != 1 or text.count(_END) != 1:
        raise ValueError(f"the scorecard needs one {_START} and one {_END}")
    table: str = text.split(_START, 1)[1].split(_END, 1)[0]
    lines: list[str] = [line.strip() for line in table.strip().splitlines()]
    rows: list[Row] = []
    for line in lines[2:]:
        cells: list[str] = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) != 4:
            raise ValueError(f"a row has {len(cells)} cells, not 4: {line}")
        feature, proven, how, _ = cells
        if feature == "" or how == "":
            raise ValueError(f"a row needs its feature and how it was proven: {line}")
        if proven == UNPROVEN:
            rows.append(Row(feature=feature, proven=None, how=how))
        elif _DATE.fullmatch(proven):
            try:
                day: date = date.fromisoformat(proven)
            except ValueError as error:
                raise ValueError(f"a row's date is no day: {line}") from error
            rows.append(Row(feature=feature, proven=day, how=how))
        else:
            raise ValueError(f"a row's date is YYYY-MM-DD or {UNPROVEN}: {line}")
    if not rows:
        raise ValueError("the scorecard has no rows")
    twice: list[str] = [name for name, count in Counter(row.feature for row in rows).items() if count > 1]
    if twice:
        raise ValueError(f"a feature is named twice: {', '.join(twice)}")
    return rows


def lapsed(rows: list[Row], today: date, limit_days: int) -> list[Row]:
    """The rows proven more than ``limit_days`` days before ``today``."""
    return [row for row in rows if row.proven is not None and (today - row.proven).days > limit_days]


def from_future(rows: list[Row], today: date) -> list[Row]:
    """The rows dated after ``today``: a date that is not a proof yet."""
    return [row for row in rows if row.proven is not None and row.proven > today]


def main() -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--today", type=date.fromisoformat, default=date.today())
    today: date = parser.parse_args().today
    try:
        rows: list[Row] = read_rows(SCORECARD.read_text(encoding="utf-8"))
    except ValueError as error:
        print(f"{SCORECARD.name}: {error}")
        return 1
    ahead: list[Row] = from_future(rows, today)
    for row in ahead:
        print(f"dated after today: {row.feature} ({row.proven}): a date is written once the proof is made")
    proven: int = sum(1 for row in rows if row.proven is not None)
    print(f"{len(rows)} features read, {proven} with a date of proof")
    return 1 if ahead else 0


if __name__ == "__main__":
    sys.exit(main())
