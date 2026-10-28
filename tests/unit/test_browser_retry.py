"""The inner retry of browser_act (item 781): a click that left the page unchanged is tried once the other way, and a
click that moved the page is not sent twice."""

from types import SimpleNamespace
from typing import cast

from dotagent.browser.types import ActOutcome, PageView, ScriptRun, Step, StepReport
from dotagent.tools.browser import ActArgs, _retry_unchanged
from dotagent.tools.context import ToolContext


class _Browser:
    """A page whose click did nothing, and a script that then moves it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    async def run_script(
        self, thread_id: str, script: str, timeout_s: float, hide_secrets: bool, refs: tuple[str, ...] = ()
    ) -> ScriptRun:
        self.calls.append(refs)
        return ScriptRun(
            end="value",
            text="true",
            url="https://example.com/after",
            surprise=None,
            seconds=0.1,
        )

    async def snapshot(self, thread_id: str, hide_secrets: bool) -> PageView:
        return PageView.model_construct(url="https://example.com/after")


def _context(browser: _Browser) -> ToolContext:
    return cast(
        ToolContext,
        SimpleNamespace(thread_id="thread", approvals="yolo", browser=browser),
    )


def _outcome(detail: str) -> ActOutcome:
    report = StepReport(index=0, description='click "Send"', outcome="done", detail=detail)
    return ActOutcome(steps=(report,), stopped=None, view=PageView.model_construct(url="https://example.com/"))


async def test_a_click_that_did_nothing_is_tried_with_a_script_once() -> None:
    browser = _Browser()
    args = ActArgs(steps=[Step(action="click", ref="e12")])
    result = await _retry_unchanged(args, _outcome("but the page did not change; nothing was sent"), _context(browser))
    assert browser.calls == [("e12",)]
    assert result.steps[0].detail.endswith("retried with a script on e12")
    assert result.view.url == "https://example.com/after"


async def test_a_click_that_moved_the_page_is_not_sent_again() -> None:
    browser = _Browser()
    args = ActArgs(steps=[Step(action="click", ref="e12")])
    original = _outcome("the page changed; it says: Saved")
    result = await _retry_unchanged(args, original, _context(browser))
    assert browser.calls == []
    assert result is original


async def test_a_click_that_sends_or_pays_is_never_clicked_again_by_the_retry() -> None:
    """The review of every module: a Send, Post or Pay button that acts slowly with nothing to see in half a second got
    a second click, past the pace and the spending counted once for the call."""
    browser = _Browser()
    args = ActArgs(steps=[Step(action="click", ref="e12")])
    original = _outcome("but the page did not change; nothing was sent")
    for flags in (("external_send", "message_send"), ("public_post",), ("financial",)):
        assert await _retry_unchanged(args, original, _context(browser), flags) is original
    assert browser.calls == []
