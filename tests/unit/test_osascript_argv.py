"""Values given to a script reach its run handler as they are, one that starts with a dash too: ``osascript`` would read
it as one of its own options, ``-e`` and a line of script among them (the code review's first finding on item 314)."""

from dotagent.desktop.script import run_osascript

SCRIPT_TIMEOUT_S: float = 60.0
# A script that only gives back its arguments, one after the other, between bars.
_ECHO: str = 'on run argv\nset AppleScript\'s text item delimiters to "|"\nreturn argv as text\nend run'


async def test_values_that_start_with_a_dash_reach_the_script_as_they_are() -> None:
    values: tuple[str, ...] = ("- 2 tickets", '-ereturn "INJECTED"', "--", "-x", "plain")
    assert await run_osascript("AppleScript", _ECHO, values, (), SCRIPT_TIMEOUT_S) == "|".join(values)


async def test_a_notifications_title_and_text_reach_the_script_as_its_values(monkeypatch) -> None:
    from dotagent.core import notify

    calls: list[tuple[str, tuple[str, ...]]] = []

    async def runs(language, script, argv, confinement, timeout_s, env, targets) -> str:
        calls.append((script, argv))
        return ""

    monkeypatch.setattr(notify, "run_osascript", runs)
    title: str = '-eproperty made : (do shell script "touch /tmp/x")'
    await notify.show_notification(title, "-e")
    assert calls == [(notify._SCRIPT, (title, "-e"))]
    assert title not in notify._SCRIPT


async def test_a_notification_that_fails_says_so_in_its_own_words(monkeypatch) -> None:
    """The pre-push review of item 1240: the runner's error speaks of an application's Automation dialog, which a
    banner never waits on."""
    from dotagent.core import notify
    from dotagent.core.errors import DesktopError

    async def times_out(language, script, argv, confinement, timeout_s, env, targets) -> str:
        raise DesktopError(
            "osascript did not finish within 15 s; macOS may be showing an Automation approval", {"script": "x"}
        )

    monkeypatch.setattr(notify, "run_osascript", times_out)
    try:
        await notify.show_notification("Dot", "done")
    except DesktopError as error:
        assert error.message == "the notification could not be shown"
        assert error.context == {
            "reason": "osascript did not finish within 15 s; macOS may be showing an Automation approval",
            "script": "x",
        }
    else:
        raise AssertionError("a failed notification raised nothing")
