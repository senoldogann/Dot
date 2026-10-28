"""The weekly eval script parses and keeps both halves of the score: the run and the comparison (item 799)."""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "weekly-eval.sh"


def test_the_weekly_eval_script_is_valid_shell_and_runs_both_steps() -> None:
    subprocess.run(["sh", "-n", str(SCRIPT)], check=True)
    text = SCRIPT.read_text(encoding="utf-8")
    assert "dotx eval run" in text
    assert "dotx eval report" in text
    assert "DOT_HOME" in text
