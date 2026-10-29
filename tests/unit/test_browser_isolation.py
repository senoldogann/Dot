"""An eval attempt gets its own home and Chrome directory, and the process environment comes back (item 805)."""

from __future__ import annotations

import os
from pathlib import Path

from dotagent.evals.runner import eval_environment, scratch_home


def test_an_eval_attempt_gets_a_home_and_a_chrome_directory_under_it() -> None:
    before_home: str | None = os.environ.get("DOT_HOME")
    before_chrome: str | None = os.environ.get("DOT_CHROME_DIR")
    with scratch_home() as home, eval_environment(home, {}):
        assert os.environ["DOT_HOME"] == str(home)
        chrome = Path(os.environ["DOT_CHROME_DIR"])
        assert chrome == home / "chrome"
        assert chrome.parent == home
    assert os.environ.get("DOT_HOME") == before_home
    assert os.environ.get("DOT_CHROME_DIR") == before_chrome
