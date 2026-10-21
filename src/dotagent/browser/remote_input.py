"""What the owner does to a held run's page from the screen that shows it (item 470): pointer, wheel and key events,
each place given as a fraction of the picture, which the page's own size in CSS pixels turns into a point, so that the
cast's scaling and Chrome's zoom do not move it. The owner acts here, not Dot: the events pass no policy, and nothing of
what is typed is kept, only how many clicks and scrolls there were and whether anything was typed."""

from collections.abc import Sequence
from typing import Annotated, Literal

from playwright.async_api import Page
from pydantic import Field

from dotagent.browser.types import Viewport
from dotagent.core.types import FrozenModel

type MouseButton = Literal["left", "right"]
# A place on the picture, from its left or top edge (0) to its right or bottom edge (1).
type Fraction = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
# How far one wheel event scrolls, in CSS pixels either way.
type WheelDelta = Annotated[float, Field(ge=-4000.0, le=4000.0, allow_inf_nan=False)]
type ClickCount = Annotated[int, Field(ge=1, le=3)]
# A key or a chord as Playwright names them: up to four modifiers, then one printable character or a named key
# ("Enter", "Backspace", "ArrowLeft", "F5", "Shift+Tab", "Meta+a").
KEY_PATTERN: str = r"^(?:(?:Shift|Control|Alt|Meta|ControlOrMeta)\+){0,4}(?:[!-~]|[A-Z][A-Za-z0-9]{1,15})$"
_KEY_CHARS: int = 32
_TEXT_CHARS: int = 500
_BATCH_EVENTS: int = 64


class PointerMove(FrozenModel):
    kind: Literal["move"]
    x: Fraction
    y: Fraction


class PointerDown(FrozenModel):
    kind: Literal["down"]
    x: Fraction
    y: Fraction
    button: MouseButton
    clicks: ClickCount


class PointerUp(FrozenModel):
    kind: Literal["up"]
    x: Fraction
    y: Fraction
    button: MouseButton
    clicks: ClickCount


class Wheel(FrozenModel):
    kind: Literal["wheel"]
    x: Fraction
    y: Fraction
    dx: WheelDelta
    dy: WheelDelta


class KeyPress(FrozenModel):
    kind: Literal["key"]
    key: str = Field(max_length=_KEY_CHARS, pattern=KEY_PATTERN)


class TextInput(FrozenModel):
    kind: Literal["text"]
    text: str = Field(min_length=1, max_length=_TEXT_CHARS)


type InputEvent = Annotated[
    PointerMove | PointerDown | PointerUp | Wheel | KeyPress | TextInput, Field(discriminator="kind")
]


class InputBatch(FrozenModel):
    """The events the screen sends at once, in the order they happened."""

    events: list[InputEvent] = Field(min_length=1, max_length=_BATCH_EVENTS)


class InputResult(FrozenModel):
    """What the page took: how many events, and the page's address and size in CSS pixels at the time."""

    accepted: int
    url: str
    width: int
    height: int


class InputTally(FrozenModel):
    """What the owner did on the page during one hold, as counts only (item 470): presses of a button, wheel events,
    whether anything was typed, and the buttons still down, which are let go when the run is handed back."""

    clicks: int
    scrolls: int
    typed: bool
    buttons_down: frozenset[MouseButton]


NO_INPUT: InputTally = InputTally(clicks=0, scrolls=0, typed=False, buttons_down=frozenset())


def viewport_point(x: float, y: float, viewport: Viewport) -> tuple[float, float]:
    """The point in CSS pixels of the page that the fractions ``x`` and ``y`` of its picture name."""
    return x * viewport.width, y * viewport.height


def tally_after(tally: InputTally, events: Sequence[InputEvent]) -> InputTally:
    """``tally`` with ``events`` counted in: a press is a click, a wheel event a scroll, a key or text is typing."""
    clicks: int = tally.clicks
    scrolls: int = tally.scrolls
    typed: bool = tally.typed
    down: set[MouseButton] = set(tally.buttons_down)
    for event in events:
        match event:
            case PointerDown(button=button):
                clicks += 1
                down.add(button)
            case PointerUp(button=button):
                down.discard(button)
            case Wheel():
                scrolls += 1
            case KeyPress() | TextInput():
                typed = True
            case PointerMove():
                pass
    return InputTally(clicks=clicks, scrolls=scrolls, typed=typed, buttons_down=frozenset(down))


async def dispatch(page: Page, viewport: Viewport, events: Sequence[InputEvent]) -> None:
    """Do ``events`` on ``page``, in order, as Playwright's mouse and keyboard do them (Chrome's own input events):
    each pointer event moves to its point first."""
    for event in events:
        match event:
            case PointerMove(x=x, y=y):
                await page.mouse.move(*viewport_point(x, y, viewport))
            case PointerDown(x=x, y=y, button=button, clicks=clicks):
                await page.mouse.move(*viewport_point(x, y, viewport))
                await page.mouse.down(button=button, click_count=clicks)
            case PointerUp(x=x, y=y, button=button, clicks=clicks):
                await page.mouse.move(*viewport_point(x, y, viewport))
                await page.mouse.up(button=button, click_count=clicks)
            case Wheel(x=x, y=y, dx=dx, dy=dy):
                await page.mouse.move(*viewport_point(x, y, viewport))
                await page.mouse.wheel(dx, dy)
            case KeyPress(key=key):
                await page.keyboard.press(key)
            case TextInput(text=text):
                await page.keyboard.insert_text(text)


async def let_go(page: Page, buttons: frozenset[MouseButton]) -> None:
    """Let go of ``buttons``, which a press from the screen left down when its release never came."""
    for button in sorted(buttons):
        await page.mouse.up(button=button)
