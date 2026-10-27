"""Which application a name means on the desktop (item 343): a name that fits several instances of one application,
"Google Chrome" while the owner's Chrome and Dot's own both run, means the one Dot's browser works in."""

from dataclasses import replace

import pytest

from dotagent.core.errors import DesktopError
from dotagent.desktop import apps
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


def _front(app: AppInfo) -> AppInfo:
    return replace(app, frontmost=True)


def test_two_chromes_of_one_name_are_told_apart_by_the_thread_then_dots_browser_and_never_by_the_front() -> None:
    """Item 750: the owner's Chrome and Dot's own are two apps of one name and one bundle id, so that "use the bundle
    id" did not help; what the thread works in, then Dot's own, decide. What is in front or has a window does not: it
    changes between the review of a batch and its run, which could then resolve to another copy."""
    both: frozenset[int] = frozenset({101, 202})
    # The thread's own application first, though Dot's browser holds both and the other one is in front.
    running: tuple[AppInfo, ...] = (_FINDER, _chrome(101), _front(_chrome(202)))
    assert find_app("Google Chrome", running, both, current=101) == _chrome(101)
    # Dot's own Chrome (alone among the candidates in the set), though the other is in front.
    assert find_app("Google Chrome", running, frozenset({101})) == _chrome(101)
    # Neither decides: the one in front is not taken, and the error asks for the pid.
    for preferred in (frozenset[int](), both):
        with pytest.raises(DesktopError, match="use the pid"):
            find_app("Google Chrome", running, preferred)
    # A thread working in an application that does not fit the name decides nothing.
    assert find_app("Google Chrome", running, frozenset({101}), current=90) == _chrome(101)


def test_the_error_of_an_ambiguous_name_lists_every_pid_and_which_is_dots_own() -> None:
    with pytest.raises(DesktopError) as caught:
        find_app("Google Chrome", _RUNNING, frozenset({101, 202}))
    assert "use the pid" in caught.value.message and "use the bundle id" not in caught.value.message
    listed: str = str(caught.value.context["candidates"])
    assert "pid 101, a Chrome Dot works in" in listed and "pid 202, a Chrome Dot works in" in listed
    with pytest.raises(DesktopError) as plain:
        find_app("Google Chrome", _RUNNING, frozenset())
    assert str(plain.value.context["candidates"]) == "Google Chrome (pid 101), Google Chrome (pid 202)"


def test_a_path_that_fits_two_copies_is_told_apart_as_a_name_is() -> None:
    assert find_app(_CHROME_PATH, _RUNNING, frozenset(), current=202) == _chrome(202)


def test_the_application_a_thread_works_in_is_what_a_name_of_two_copies_means(monkeypatch: pytest.MonkeyPatch) -> None:
    from dotagent.desktop import memory
    from dotagent.desktop.session import _pick_app

    state = memory.targeting(memory.fresh_thread(), 101)
    assert _pick_app(state, "Google Chrome", _RUNNING, frozenset({101, 202})).pid == 101
    with pytest.raises(DesktopError, match="use the pid"):
        _pick_app(memory.fresh_thread(), "Google Chrome", _RUNNING, frozenset({101, 202}))


def test_a_running_application_is_brought_forward_by_its_pid_and_not_by_its_bundle_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Item 750: ``open -b com.google.Chrome`` may reach the other copy; the process asked to come forward is meant."""
    told: list[str] = []

    class _Process:
        def unhide(self) -> None:
            told.append("unhide")

        def activateWithOptions_(self, options: int) -> None:
            told.append(f"activate {options}")

    copies: list[int] = [101, 202]

    class _Running:
        @staticmethod
        def runningApplicationWithProcessIdentifier_(pid: int) -> _Process | None:
            told.append(f"pid {pid}")
            return _Process() if pid == 202 else None

        @staticmethod
        def runningApplicationsWithBundleIdentifier_(bundle_id: str) -> list[int]:
            return copies

    # The front stays elsewhere through the accessibility try, then the process's own activation brings it forward.
    waits: list[bool] = [False, True, False, True]
    monkeypatch.setattr(apps, "frontmost_pid", lambda: 90)
    monkeypatch.setattr(apps, "_raise_through_accessibility", lambda app: told.append("accessibility"))
    monkeypatch.setattr(apps.AppKit, "NSRunningApplication", _Running)
    monkeypatch.setattr(apps, "launch", lambda query: told.append(f"launch {query}"))
    monkeypatch.setattr(apps, "_wait_for_front", lambda pid, timeout: waits.pop(0))
    apps.activate(_chrome(202), 1.0)
    assert told == ["accessibility", "pid 202", "unhide", "activate 3"]
    # A process that no longer runs is started through Launch Services, by its bundle id.
    told.clear()
    apps.activate(_chrome(303), 1.0)
    assert told == ["accessibility", "pid 303", "launch com.google.Chrome"]
    # One copy alone is brought forward through Launch Services, as before: from Dot's background process the
    # process's own activation may be refused.
    copies.pop()
    told.clear()
    waits.extend([False, True])
    apps.activate(_chrome(202), 1.0)
    assert told == ["accessibility", "launch com.google.Chrome"]


def test_an_activate_step_brings_forward_the_copy_its_review_judged(monkeypatch: pytest.MonkeyPatch) -> None:
    """The code review of item 750: the inspection of `activate "Google Chrome"` picked the thread's own copy (the
    owner's Chrome) while the step resolved the name again without it and brought Dot's own forward, so the batch's
    next keys went to a copy no review had seen. The step resolves as the inspection did, and refuses another copy."""
    from types import SimpleNamespace
    from typing import cast

    from dotagent.config import load_default_config
    from dotagent.desktop import actions
    from dotagent.desktop.types import Step

    brought: list[int] = []
    monkeypatch.setattr(apps, "list_apps", lambda: _RUNNING)
    monkeypatch.setattr(apps, "activate", lambda app, timeout_s: brought.append(app.pid))
    env: actions.StepEnv = cast(
        actions.StepEnv,
        SimpleNamespace(app=_chrome(101), preferred=frozenset({202}), config=load_default_config().desktop),
    )
    step: Step = Step(action="activate", text="Google Chrome")
    judged: actions.Inspection = cast(actions.Inspection, SimpleNamespace(pid=101))
    assert actions._activate(env, step, judged).switched_to == _chrome(101)
    assert brought == [101]
    with pytest.raises(DesktopError, match="not the copy the step was judged for"):
        actions._activate(env, step, cast(actions.Inspection, SimpleNamespace(pid=202)))
    assert brought == [101]
