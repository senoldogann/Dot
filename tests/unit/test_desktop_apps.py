"""Which application a name means on the desktop (item 343): a name that fits several instances of one application,
"Google Chrome" while the owner's Chrome and Dot's own both run, means the one Dot's browser works in."""

import pytest

from dotagent.core.errors import DesktopError
from dotagent.desktop.apps import find_app
from dotagent.desktop.types import AppInfo

_CHROME_PATH: str = "/Applications/Google Chrome.app"
_FINDER: AppInfo = AppInfo(
    pid=90,
    name="Finder",
    bundle_id="com.apple.finder",
    bundle_path="/System/Library/CoreServices/Finder.app",
    kind="regular",
    frontmost=True,
)


def _chrome(pid: int) -> AppInfo:
    return AppInfo(
        pid=pid,
        name="Google Chrome",
        bundle_id="com.google.Chrome",
        bundle_path=_CHROME_PATH,
        kind="regular",
        frontmost=False,
    )


_RUNNING: tuple[AppInfo, ...] = (_FINDER, _chrome(101), _chrome(202))


def test_a_name_that_fits_two_chromes_means_the_one_dots_browser_works_in() -> None:
    assert find_app("Google Chrome", _RUNNING, frozenset({202})) == _chrome(202)
    assert find_app("chrome", _RUNNING, frozenset({101})) == _chrome(101)
    assert find_app("com.google.Chrome", _RUNNING, frozenset({202})) == _chrome(202)


def test_with_no_chrome_of_dots_among_them_the_name_names_several() -> None:
    for preferred in (frozenset[int](), frozenset({303})):
        with pytest.raises(DesktopError, match="names several applications"):
            find_app("Google Chrome", _RUNNING, preferred)


def test_a_pid_or_a_name_of_one_application_is_what_it_was() -> None:
    assert find_app("202", _RUNNING, frozenset({101})) == _chrome(202)
    assert find_app("Finder", _RUNNING, frozenset({101})) == _FINDER


def test_the_bundle_a_path_leads_to_is_the_application_it_names() -> None:
    """Item 418: ``desktop_open_app`` opens the bundle its check resolved the name or link to, so a path names the
    application that sits at it, and several copies of that one application are told apart as a name tells them."""
    assert find_app(_CHROME_PATH, _RUNNING, frozenset({202})) == _chrome(202)
    assert find_app("/System/Library/CoreServices/Finder.app", _RUNNING, frozenset()) == _FINDER
    assert find_app("/Applications/Safari.app", _RUNNING, frozenset()) is None
    with pytest.raises(DesktopError, match="names several applications"):
        find_app(_CHROME_PATH, _RUNNING, frozenset())
