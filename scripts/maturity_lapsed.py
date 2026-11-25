"""List the features of Dot's maturity scorecard (``docs/maturity.md``, item 1857) whose proof is more than
:data:`check_maturity.LIMIT_DAYS` days old, as the body of a GitHub issue, and exit 1 when there is one. It runs every
day in ``.github/workflows/maturity.yml``, which keeps one issue open with the list while any lapsed and closes it when
none does; never in the gate, which a date passing with no change of code must not fail.

Usage: uv run python scripts/maturity_lapsed.py [--today YYYY-MM-DD]
"""

import argparse
import sys
from datetime import date

from check_maturity import LIMIT_DAYS, SCORECARD, UNPROVEN, Row, lapsed, read_rows


def issue_body(stale: list[Row], today: date, limit_days: int) -> str:
    """The issue's text for the rows ``stale`` as of ``today``."""
    lines: list[str] = [
        f"These features of `docs/maturity.md` were last proven on the real Mac more than {limit_days} days before"
        f" {today.isoformat()}. Prove each again with a real run of the owner's Dot and write its date and how, or mark"
        f" it `{UNPROVEN}` with the date it was last proven; never move a date forward without a new proof.",
        "",
    ]
    lines.extend(f"- **{row.feature}**: last proven {row.proven} ({row.how})" for row in stale)
    return "\n".join(lines) + "\n"


def main() -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--today", type=date.fromisoformat, default=date.today())
    today: date = parser.parse_args().today
    stale: list[Row] = lapsed(read_rows(SCORECARD.read_text(encoding="utf-8")), today, LIMIT_DAYS)
    if not stale:
        print(f"no feature's proof is older than {LIMIT_DAYS} days")
        return 0
    print(issue_body(stale, today, LIMIT_DAYS), end="")
    return 1


if __name__ == "__main__":
    sys.exit(main())
