"""An osascript failure is read by its error number, which macOS writes the same in every language (item 1216)."""

import pytest

from dotagent.core.errors import DesktopError
from dotagent.desktop.script import OSASCRIPT_ERRORS, osascript_error, run_osascript


def test_the_number_at_the_end_names_the_failure_whatever_the_language() -> None:
    english: str = "execution error: Not authorized to send Apple events to Notes. (-1743)"
    turkish: str = "yürütme hatası: Notes uygulamasına Apple olayları göndermeye yetkili değil. (-1743)"
    assert osascript_error(english) == osascript_error(turkish) == (-1743, OSASCRIPT_ERRORS[-1743])
    finder: tuple[int, str] | None = osascript_error('execution error: Can’t get window 1 of "Finder". (-1728)\n')
    assert finder is not None and finder[0] == -1728


def test_a_number_not_known_or_none_at_all_says_nothing_new() -> None:
    assert osascript_error("execution error: something odd (-99999)") is None
    assert osascript_error("syntax trouble without a number") is None
    assert osascript_error("") is None


@pytest.mark.macos
async def test_a_failing_script_says_the_meaning_of_its_number() -> None:
    with pytest.raises(DesktopError) as raised:
        await run_osascript("AppleScript", 'error "made up" number -2700', (), (), 10.0)
    assert raised.value.message.startswith("osascript failed with error -2700:")
    assert "(-2700)" in str(raised.value.context["stderr"])
