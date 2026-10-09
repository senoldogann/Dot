"""Value types shared by the browser connector and the browser tools."""

from collections.abc import Awaitable, Callable
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dotagent.core.types import FrozenModel

type StepAction = Literal[
    "click",
    "double_click",
    "right_click",
    "hover",
    "fill",
    "type",
    "select",
    "press",
    "check",
    "uncheck",
    "scroll_into_view",
    "click_at",
]

_REF_ACTIONS: frozenset[str] = frozenset(
    {
        "click",
        "double_click",
        "right_click",
        "hover",
        "fill",
        "type",
        "select",
        "check",
        "uncheck",
        "scroll_into_view",
    }
)
_TEXT_ACTIONS: frozenset[str] = frozenset({"fill", "type", "select"})
_SIDE_EFFECT_ACTIONS: frozenset[str] = frozenset(
    {"click", "double_click", "right_click", "select", "press", "check", "uncheck", "click_at"}
)


class Step(BaseModel):
    """One browser step. Which fields apply depends on ``action``; the others must be left out."""

    model_config = ConfigDict(extra="forbid")

    action: StepAction = Field(
        description=(
            "click | double_click | right_click | hover | fill (replace the value of a field) | type (key by key, for "
            "autocomplete widgets) | select (choose an option by value or label) | press (a key or chord) | check | "
            "uncheck | scroll_into_view | click_at (viewport pixel x,y from a screenshot, for canvases)."
        )
    )
    ref: str | None = Field(default=None, description="Element ref from the latest snapshot, for example e12.")
    text: str | None = Field(
        default=None,
        description=(
            "Text for fill and type. For select: the label or value of one of the options the snapshot lists, written "
            "exactly as listed."
        ),
    )
    key: str | None = Field(default=None, description="Key or chord for press, for example Enter or Control+A.")
    x: float | None = Field(default=None, description="Viewport x in CSS pixels, for click_at.")
    y: float | None = Field(default=None, description="Viewport y in CSS pixels, for click_at.")

    @model_validator(mode="after")
    def _check_fields(self) -> Self:
        action: str = self.action
        if action in _REF_ACTIONS and self.ref is None:
            raise ValueError(f"{action} needs a ref")
        if action in _TEXT_ACTIONS and self.text is None:
            raise ValueError(f"{action} needs text")
        if action == "press" and self.key is None:
            raise ValueError("press needs a key")
        if action == "click_at" and (self.x is None or self.y is None or self.ref is not None):
            raise ValueError("click_at needs x and y and no ref")
        unused: list[str] = []
        if self.text is not None and action not in _TEXT_ACTIONS:
            unused.append("text")
        if self.key is not None and action != "press":
            unused.append("key")
        if (self.x is not None or self.y is not None) and action != "click_at":
            unused.append("x/y")
        if self.ref is not None and action not in _REF_ACTIONS and action != "press":
            unused.append("ref")
        if unused:
            raise ValueError(f"{action} does not use {', '.join(unused)}")
        return self

    def has_side_effects(self) -> bool:
        return self.action in _SIDE_EFFECT_ACTIONS


class StepReport(FrozenModel):
    index: int
    description: str
    outcome: Literal["done", "failed", "skipped"]
    detail: str


class DropdownOption(FrozenModel):
    """One option of a native dropdown; the texts come from the page."""

    label: str
    value: str
    selected: bool
    disabled: bool


class Dropdown(FrozenModel):
    """The options of a native ``<select>`` element in page order; they come from the page."""

    options: tuple[DropdownOption, ...]


class TabSummary(FrozenModel):
    index: int
    url: str
    title: str
    active: bool


class PageView(FrozenModel):
    """What the model is shown about the current tab; ``title``, ``body``, ``overlay`` and ``notes`` come from the page.

    ``overlay`` is what stands in front of the page (open dialogs, cookie or newsletter boxes) and ``overlays`` how
    many such boxes are open; both are about the whole viewport, also in a view that shows only the changes."""

    tab_index: int
    url: str
    title: str
    status: int | None
    mode: Literal["full", "delta"]
    body: str
    overlay: str
    overlays: int
    offscreen_refs: int
    tabs: tuple[TabSummary, ...]
    notes: tuple[str, ...]


class ActOutcome(FrozenModel):
    steps: tuple[StepReport, ...]
    stopped: str | None
    view: PageView


class TextWait(FrozenModel):
    """What a wait for a text found: whether the text became visible in time, how long the wait took (the whole wait
    when it did not appear) and the page after it."""

    appeared: bool
    waited_s: float
    view: PageView


class TextView(FrozenModel):
    """A slice of the visible text of a page or element."""

    url: str
    text: str
    offset: int
    total_chars: int


class FindResult(FrozenModel):
    """Elements of the current page that match a search; ``hits`` come from the page."""

    url: str
    hits: tuple[str, ...]


# The secret a field takes, as the page marks it: a password input, or one of these autocomplete tokens.
SecretKind = Literal[
    "password",
    "current-password",
    "new-password",
    "one-time-code",
    "cc-number",
    "cc-csc",
    "cc-exp",
    "cc-exp-month",
    "cc-exp-year",
]


class SecretField(FrozenModel):
    """A field that takes a secret, at the moment a step is about to type into it, on the page at ``url``. ``ref`` is
    the ref the step names, ``None`` for a key pressed without one."""

    ref: str | None
    kind: SecretKind
    url: str


# What a step does at a field that takes a secret: it returns to let the text in, and raises to refuse.
type SecretFieldCheck = Callable[[SecretField], Awaitable[None]]


class Viewport(FrozenModel):
    """The page area that is on screen, in CSS pixels."""

    width: int
    height: int


class Attachment(FrozenModel):
    """The Chrome of the user that a session works in."""

    browser_version: str
    open_tabs: int


class Screenshot(FrozenModel):
    """A JPEG of the viewport; one pixel is one CSS pixel, so coordinates in it work with ``click_at``."""

    url: str
    width: int
    height: int
    data: bytes
