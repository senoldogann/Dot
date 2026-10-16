"""Values given to a script reach its run handler as they are, one that starts with a dash too: ``osascript`` would read
it as one of its own options, ``-e`` and a line of script among them (the code review's first finding on item 314)."""

from dotagent.desktop.script import run_osascript

SCRIPT_TIMEOUT_S: float = 60.0
# A script that only gives back its arguments, one after the other, between bars.
_ECHO: str = 'on run argv\nset AppleScript\'s text item delimiters to "|"\nreturn argv as text\nend run'


async def test_values_that_start_with_a_dash_reach_the_script_as_they_are() -> None:
    values: tuple[str, ...] = ("- 2 tickets", '-ereturn "INJECTED"', "--", "-x", "plain")
    assert await run_osascript("AppleScript", _ECHO, values, (), SCRIPT_TIMEOUT_S) == "|".join(values)
