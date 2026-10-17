"""A fix for a bug in Playwright's Python client that would otherwise end runs.

Pages that add and remove many iframes quickly (ads, sign-in widgets) can make the client handle the detachment of one
frame twice. The second time, ``Page._on_frame_detached`` fails on ``self._frames.remove(frame)``; Playwright keeps that
``ValueError`` and raises it at the next API call of the connection, whatever that call is ("Locator.count:
list.remove(x): x not in list"). See https://github.com/microsoft/playwright-python/issues/1268 (open since 1.20, still
in 1.63). A frame is detached only once, so a detachment of a frame the page no longer lists is a repeat and is ignored.
"""

from playwright._impl._frame import Frame
from playwright._impl._page import Page

_original_on_frame_detached = Page._on_frame_detached


def _on_frame_detached_once(page: Page, frame: Frame) -> None:
    if frame not in page._frames:
        return
    _original_on_frame_detached(page, frame)


def ignore_repeated_frame_detach() -> None:
    """Install the fix; installing it again changes nothing."""
    Page._on_frame_detached = _on_frame_detached_once  # type: ignore[method-assign, assignment]
