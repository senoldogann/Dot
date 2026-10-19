"""Dot's window brought to the front after a capture of SnapShots (item 414), with the capture in its box: the desktop
app when it runs; else the tab of a browser that shows Dot's pages, found and chosen with AppleScript (Safari, Chrome
and the browsers built on it, Arc), the browser the page that took the last capture runs in first; else Dot opened in
the default browser. macOS asks once whether Dot may drive a browser (Privacy & Security › Automation); a browser it
may not drive, or Firefox, which has no words for its tabs, is only brought to the front. Blocking: the service calls
it off the event loop."""

import subprocess
import time
from collections.abc import Callable, Sequence
from typing import Literal

from dotagent.core.errors import DesktopError
from dotagent.core.logs import get_logger
from dotagent.desktop import apps, screen
from dotagent.desktop.flight import Frame
from dotagent.desktop.types import AppInfo, WindowInfo

log = get_logger(__name__)

# The desktop app (desktop/, its bundle identifier in tauri.conf.json).
DESKTOP_APP: str = "dev.dot.desktop"
type TabWords = Literal["chrome", "safari", "arc"]
# The browsers whose tabs AppleScript reaches, by bundle identifier, and the words of each.
BROWSERS: dict[str, TabWords] = {
    "com.apple.Safari": "safari",
    "com.apple.SafariTechnologyPreview": "safari",
    "com.google.Chrome": "chrome",
    "com.google.Chrome.canary": "chrome",
    "org.chromium.Chromium": "chrome",
    "com.brave.Browser": "chrome",
    "com.microsoft.edgemac": "chrome",
    "com.vivaldi.Vivaldi": "chrome",
    "company.thebrowser.Browser": "arc",
}
_FIREFOX: str = "org.mozilla.firefox"
_OSASCRIPT: str = "/usr/bin/osascript"
_OPEN: str = "/usr/bin/open"
# The first time, macOS's question whether Dot may drive the browser waits for the owner.
_SCRIPT_TIMEOUT_S: float = 60.0
_OPEN_TIMEOUT_S: float = 10.0
# How long the window that came to the front gets to be in front, read this often.
_FRONT_WITHIN_S: float = 1.0
_FRONT_EVERY_S: float = 0.05

# Each script runs with what a tab of Dot's starts with as its arguments (:func:`tab_prefixes`), and says "found" or
# "none".
_SCRIPTS: dict[TabWords, str] = {
    "chrome": """on run argv
  tell application id "{bundle}"
    repeat with w in windows
      set i to 0
      repeat with t in tabs of w
        set i to i + 1
        set u to URL of t
        repeat with p in argv
          if u starts with (p as text) then
            set active tab index of w to i
            set index of w to 1
            activate
            return "found"
          end if
        end repeat
      end repeat
    end repeat
  end tell
  return "none"
end run""",
    "safari": """on run argv
  tell application id "{bundle}"
    repeat with w in windows
      repeat with t in tabs of w
        set u to URL of t
        if u is not missing value then
          repeat with p in argv
            if u starts with (p as text) then
              set current tab of w to t
              set index of w to 1
              activate
              return "found"
            end if
          end repeat
        end if
      end repeat
    end repeat
  end tell
  return "none"
end run""",
    "arc": """on run argv
  tell application id "{bundle}"
    repeat with w in windows
      repeat with t in tabs of w
        set u to URL of t
        repeat with p in argv
          if u starts with (p as text) then
            tell t to select
            activate
            return "found"
          end if
        end repeat
      end repeat
    end repeat
  end tell
  return "none"
end run""",
}


def browsers_first(agent: str | None, running: Sequence[str]) -> list[str]:
    """The running browsers of :data:`BROWSERS` (bundle identifiers) in the order to look for Dot's tab: the family of
    the browser ``agent`` (a page's User-Agent) names first, so that macOS asks about the one in use, then the
    others."""
    known: list[str] = [bundle for bundle in BROWSERS if bundle in running]
    if agent is None:
        return known
    family: set[str]
    if "Edg/" in agent:
        family = {"com.microsoft.edgemac"}
    elif "Chrome/" in agent:
        family = {bundle for bundle, words in BROWSERS.items() if words in ("chrome", "arc")}
    elif "Safari/" in agent:
        family = {bundle for bundle, words in BROWSERS.items() if words == "safari"}
    else:
        family = set()
    return [bundle for bundle in known if bundle in family] + [bundle for bundle in known if bundle not in family]


def tab_prefixes(addresses: Sequence[str]) -> list[str]:
    """What the address of a tab that shows one of Dot's pages starts with: each of ``addresses`` with the ``/`` after
    its host, so that a site whose name merely starts with Dot's (``dot.example.net`` for ``dot.example``) is not taken
    for Dot's."""
    return [f"{address.rstrip('/')}/" for address in addresses]


def _run(arguments: list[str], timeout_s: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, capture_output=True, text=True, timeout=timeout_s, check=False)


def _open(arguments: list[str]) -> None:
    """``open`` with ``arguments``; a DesktopError when it fails or does not answer in time, whose context names the
    arguments without the fragment of an address, where the UI's token travels."""
    shown: str = " ".join(argument.split("#", 1)[0] for argument in arguments)
    try:
        done: subprocess.CompletedProcess[str] = _run([_OPEN, *arguments], _OPEN_TIMEOUT_S)
    except subprocess.TimeoutExpired as error:
        raise DesktopError("open did not answer in time", {"arguments": shown}) from error
    if done.returncode != 0:
        raise DesktopError("open could not bring Dot forward", {"arguments": shown, "error": done.stderr.strip()[:300]})


type TabFound = Literal["found", "none", "unasked"]


def _choose_tab(bundle: str, addresses: Sequence[str]) -> TabFound:
    """Whether a tab of ``bundle`` shows one of ``addresses`` and was chosen, its window brought to the front
    (``found``), none does (``none``), or the browser could not be asked: macOS has not let Dot drive it (-1743,
    Privacy & Security › Automation), or it did not answer in time (``unasked``)."""
    script: str = _SCRIPTS[BROWSERS[bundle]].replace("{bundle}", bundle)
    try:
        done: subprocess.CompletedProcess[str] = _run(
            [_OSASCRIPT, "-e", script, *tab_prefixes(addresses)], _SCRIPT_TIMEOUT_S
        )
    except subprocess.TimeoutExpired:
        log.warning("dot_tab_unasked", browser=bundle, error="the browser did not answer in time")
        return "unasked"
    if done.returncode != 0:
        log.warning("dot_tab_unasked", browser=bundle, error=done.stderr.strip()[:300])
        return "unasked"
    return "found" if done.stdout.strip() == "found" else "none"


def _front_frame(pid: int | None, before: int | None) -> Frame | None:
    """The frame of the window in front once the application ``pid`` has it, or, for None, any application but
    ``before`` (the one in front before Dot was asked for, the one just captured); None when none came."""
    deadline: float = time.monotonic() + _FRONT_WITHIN_S
    while True:
        front: int | None = apps.frontmost_pid()
        if front is not None and (front == pid if pid is not None else front != before):
            windows: tuple[WindowInfo, ...] = screen.windows_on_screen()
            window: WindowInfo | None = next(
                (one for one in windows if one.pid == front and one.layer == 0 and one.bounds.width > 1), None
            )
            if window is not None:
                bounds = window.bounds
                return Frame(x=bounds.x, y=bounds.y, width=bounds.width, height=bounds.height)
        if time.monotonic() >= deadline:
            return None
        time.sleep(_FRONT_EVERY_S)


def show_dot(addresses: Sequence[str], fallback_url: str, agent: str | None) -> Frame | None:
    """Bring Dot's window to the front: the desktop app, a browser's tab that shows one of ``addresses`` (the browser
    of the page's User-Agent ``agent`` first), else ``fallback_url`` opened in the default browser. Returns the frame
    of the window that came to the front, None when none did."""
    running: dict[str, AppInfo] = {app.bundle_id: app for app in apps.list_apps() if app.bundle_id is not None}
    before: int | None = apps.frontmost_pid()
    if DESKTOP_APP in running:
        _open(["-b", DESKTOP_APP])
        return _front_frame(running[DESKTOP_APP].pid, before)
    if agent is not None and "Firefox/" in agent and _FIREFOX in running:
        # Firefox has no words for its tabs: it comes to the front as it is, Dot's tab most likely the one it shows.
        _open(["-b", _FIREFOX])
        return _front_frame(running[_FIREFOX].pid, before)
    unasked: list[str] = []
    for bundle in browsers_first(agent, list(running)):
        found: TabFound = _choose_tab(bundle, addresses)
        if found == "found":
            return _front_frame(running[bundle].pid, before)
        if found == "unasked":
            unasked.append(bundle)
    if unasked:
        # A browser that could not be asked may well show Dot: it comes to the front as it is, rather than Dot opening
        # anew at every capture.
        _open(["-b", unasked[0]])
        return _front_frame(running[unasked[0]].pid, before)
    log.info("dot_tab_opened", reason="no browser showed Dot's pages")
    _open([fallback_url])
    return _front_frame(None, before)


type Shower = Callable[[str | None], Frame | None]


def dot_shower(addresses: Sequence[str], fallback_url: str) -> Shower:
    """What the service brings Dot forward with: :func:`show_dot` at ``addresses``, given the page's User-Agent."""

    def show(agent: str | None) -> Frame | None:
        return show_dot(addresses, fallback_url, agent)

    return show
