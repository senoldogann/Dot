"""What the harness page and the comments of the defaults say of the loop's rules is what the code does (item 589,
pre-push review round 4): the rule of selection is never given without its margin, and the budget the defaults name is
the one a step and the confirmation of ``auto`` cost.

The page and the defaults are read as text: a sentence that states a rule is one a change of the rule has to find.
"""

import re
from pathlib import Path

from dotagent.config import load_default_config
from dotagent.harness.contrast import CONFIRM_FACTOR

ROOT: Path = Path(__file__).resolve().parents[2]
PAGE: Path = ROOT / "docs" / "harness.md"
DEFAULTS: Path = ROOT / "src" / "dotagent" / "defaults.toml"
# What a run of a probe costs in tokens with Dot's prompt: measured on 2026-10-19 with Claude Haiku 5.5, which the page
# and the comment of the defaults say. The figures they give for a step are this times the runs of the defaults.
TOKENS_A_RUN: int = 57_000


def _page_blocks() -> list[str]:
    """The page's paragraphs and list items, each as one line of words."""
    blocks: list[str] = []
    words: list[str] = []
    for line in PAGE.read_text(encoding="utf-8").split("\n"):
        if words and (line.strip() == "" or re.match(r"\s*\d+\. \*\*", line)):
            blocks.append(" ".join(words))
            words = []
        if line.strip():
            words.append(line.strip())
    return [*blocks, *([" ".join(words)] if words else [])]


def _comment_blocks() -> list[str]:
    """The comments of the defaults, each run of lines that start with ``#`` as one line of words."""
    blocks: list[str] = []
    words: list[str] = []
    for line in DEFAULTS.read_text(encoding="utf-8").split("\n"):
        if line.startswith("#"):
            words.append(line.removeprefix("#").strip())
        elif words:
            blocks.append(" ".join(words))
            words = []
    return [*blocks, *([" ".join(words)] if words else [])]


def test_wherever_the_docs_state_the_rule_of_selection_they_name_the_margin() -> None:
    """The step as built said that a candidate that passed more on a task and less on none survives: false since the
    margin, which makes a gain of one attempt unclear. Each paragraph that says "on none" (the rule: more on a task,
    fewer on none) says the margin too."""
    stated: list[str] = [block for block in (*_page_blocks(), *_comment_blocks()) if re.search(r"\bon none\b", block)]
    # The page's Selection, the Adopt step and the step as built, and the comment of the defaults: the check sees them.
    assert len(stated) >= 4, [block[:50] for block in stated]
    assert [block[:70] for block in stated if "margin" not in block] == []


def test_the_default_budget_covers_a_step_and_not_the_confirmation_of_a_composed_survivor() -> None:
    """The confirmation that ``auto`` adds plays the base and the survivor again on twice as many attempts, as much
    again as the step: at the measured cost of a run, a step with a composed survivor needs about 9 million tokens, over
    the 8 million of the default, and one with one winner (no composition) just fits. The default is not raised (a
    budget is the owner's money): the page and the comment say what it covers, and this pins the arithmetic they
    give."""
    config = load_default_config().harness
    # A text is scored on the guards, the tasks with headroom and the saturated ones that check nothing breaks (item
    # 719): at most as many tasks as the ten of the scoring set before it.
    scored: int = len(config.scoring_tasks) + config.headroom_tasks + config.regression_tasks
    runs_a_text: int = scored * config.step_repeats
    # The base and a candidate in each scope, then the composition of two winners, then the confirmation: the base and
    # the survivor again on CONFIRM_FACTOR times the attempts.
    before_composing: int = (1 + len(config.step_candidates)) * runs_a_text
    composed_step: int = before_composing + runs_a_text
    confirming: int = 2 * runs_a_text * CONFIRM_FACTOR
    assert (runs_a_text, composed_step, confirming) == (20, 80, 80)
    assert composed_step * TOKENS_A_RUN <= config.step_tokens
    assert (before_composing + confirming) * TOKENS_A_RUN <= config.step_tokens
    assert (composed_step + confirming) * TOKENS_A_RUN > config.step_tokens
    assert config.step_tokens == 8_000_000
    # The figures the two texts give.
    figures: tuple[float, int] = (
        round(composed_step * TOKENS_A_RUN / 1e6, 1),
        round((composed_step + confirming) * TOKENS_A_RUN / 1e6),
    )
    assert figures == (4.6, 9)
    page: str = " ".join(PAGE.read_text(encoding="utf-8").split())
    defaults: str = " ".join(DEFAULTS.read_text(encoding="utf-8").replace("\n#", "").split())
    for text in (page, defaults):
        assert "about 4.6 million" in text and "about 9 million" in text
        assert "raise `step_tokens`" in text or "raise it with auto" in text
