"""What `browser_captcha` presses is weighed by the elements a snapshot shows under its pixel, as a `click_at` of
`browser_act` is (item 1252): before this, the tool had one fixed rating - a click, low risk - so a page could label a
button that pays like a human check and have it pressed without the policy ever seeing a payment (item 1288).

It weighs the pixel itself and refuses, rather than asking the user: the tool runs under the browser's lock, where a
question would hold every other call on that thread.
"""

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from dotagent.agent.verify import new_facts
from dotagent.browser.snapshot import line_for_ref, parse_snapshot, refs_at_point
from dotagent.core.errors import ToolExecutionError
from dotagent.tools.browser import _refuse_a_flagged_press
from dotagent.tools.context import ToolContext

PAGE: str = """- main [ref=e1] [box=0,0,800,600]:
  - form "Checkout" [ref=e2] [box=100,100,400,300]:
    - button "Pay now 249,00 €" [ref=e3] [box=150,300,160,40]
  - button "Verify you are human" [ref=e4] [box=150,400,160,40]
  - canvas [ref=e5] [box=600,400,100,100]"""


def _context(page: str) -> ToolContext:
    lines = parse_snapshot(page)

    browser = SimpleNamespace(
        peek_at=lambda thread_id, x, y: refs_at_point(lines, x, y),
        peek_line=lambda thread_id, ref: line_for_ref(lines, ref),
    )
    return ToolContext(
        browser=cast(Any, browser),
        run_id="run-1",
        agent_id=None,
        thread_id="thread-1",
        mode="task",
        approvals="ask",
        questions="leave",
        workspace=Path(),
        gate=cast(Any, SimpleNamespace(add_instruction=lambda text: None)),
        cancel=asyncio.Event(),
        output_chars=4_000,
        facts=new_facts,
        runtime=cast(Any, SimpleNamespace()),
    )


def test_a_check_a_page_draws_over_a_paying_button_is_never_pressed() -> None:
    """A page that labels a button that pays like a human check: nothing is pressed, and the run is told where to go."""
    with pytest.raises(ToolExecutionError) as refused:
        _refuse_a_flagged_press(230, 320, _context(PAGE))

    assert "Pay now 249,00 €" in str(refused.value)
    assert "browser_act" in str(refused.value)


def test_a_real_check_is_pressed() -> None:
    _refuse_a_flagged_press(200, 420, _context(PAGE))


def test_a_pixel_the_snapshot_draws_nothing_at_is_the_click_alone() -> None:
    _refuse_a_flagged_press(900, 900, _context(PAGE))
