"""The browser's closed pages and a launch that cannot work (item 57): what became of a popup that could not be
adopted, what the model reads when a tab closes under it, and a file path refused before Launch Services retries it."""

import pytest
from playwright.async_api import Error as PlaywrightError

from dotagent.browser.session import closed_popup_note, closed_tab_stop, popup_fate
from dotagent.core.errors import BrowserError, DesktopError
from dotagent.desktop.apps import open_arguments


def test_a_popup_that_closed_itself_is_closed_whatever_the_error_and_a_gone_browser_is_gone() -> None:
    focus_failed: BrowserError = BrowserError("keeping the page focused failed", {"error": "Target page closed"})
    assert popup_fate(focus_failed, True) == "closed"
    assert popup_fate(PlaywrightError("Target page, context or browser has been closed"), True) == "closed"
    assert popup_fate(PlaywrightError("Target page, context or browser has been closed"), False) == "gone"
    # Any other error on a page that is still open is a bug and is raised by the caller.
    assert popup_fate(focus_failed, False) is None
    assert popup_fate(ValueError("x"), False) is None


def test_the_notes_name_the_page_and_say_where_the_model_stands() -> None:
    assert closed_popup_note("https://a.example/callback?code=1") == (
        "a tab opened and closed itself (https://a.example/callback?code=1); there is no tab to switch to"
    )
    assert closed_tab_stop("https://a.example/done") == (
        "the tab was closed by the page (it was at https://a.example/done); the tab now current is shown below"
    )


def test_open_takes_a_bundle_a_bundle_id_or_a_name_and_a_file_path_is_refused() -> None:
    assert open_arguments("/Applications/Terminal.app") == ["/Applications/Terminal.app"]
    assert open_arguments("com.apple.Terminal") == ["-b", "com.apple.Terminal"]
    assert open_arguments("Terminal") == ["-a", "Terminal"]
    with pytest.raises(DesktopError, match="is a file path, not an application"):
        open_arguments("/Users/someone/.hermes/node/bin/node")
