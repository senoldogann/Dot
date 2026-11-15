"""Value types shared by the browser connector and the browser tools."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from dotagent.core.outcomes import EXPECT_MAX_CHARS, EXPECT_MIN_CHARS
from dotagent.core.outcomes import StepReport as StepReport
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
    "drag_at",
    "insert",
    "drag",
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
        "insert",
        "drag",
    }
)
_TEXT_ACTIONS: frozenset[str] = frozenset({"fill", "type", "select", "insert"})
# The steps that write text into a field (item 1133): written with no ref, as after a click_at on the field's pixel,
# their refusal says where the field's ref comes from.
_WRITING_ACTIONS: frozenset[str] = frozenset({"fill", "type", "insert"})
_SIDE_EFFECT_ACTIONS: frozenset[str] = frozenset(
    {"click", "double_click", "right_click", "select", "press", "check", "uncheck", "click_at", "drag_at", "drag"}
)
# The steps that only put text into a field or move the view (item 1145): they run on after a step whose check is not
# verified, as the fields of one form do not depend on each other. Every other step acts on what the steps before it
# left (a press of the form's button, a box whose change sends the form) and waits for a look.
_INDEPENDENT_ACTIONS: frozenset[str] = frozenset({"fill", "type", "insert", "hover", "scroll_into_view"})
# How a step's report begins when the page stayed as it was (item 739), which the retry of a click reads (item 781).
PAGE_UNCHANGED: str = "but the page did not change"


class Step(BaseModel):
    """One browser step. Which fields apply depends on ``action``; the others must be left out."""

    model_config = ConfigDict(extra="forbid")

    action: StepAction = Field(
        description=(
            "click | double_click | right_click | hover | fill (replace the value of a field) | type (key by key, for "
            "autocomplete widgets) | insert (put the text in at once, without keys: long text, editors) | select "
            "(choose an option by value or label) | press (a key or chord) | check | uncheck | scroll_into_view | "
            "drag (move the element ref onto to_ref) | click_at (viewport pixel x,y from a screenshot: a canvas, or a "
            "cross-origin widget like a captcha) | drag_at (press at x,y and move to to_x,to_y: a slider or a "
            "captcha's drag puzzle, where no ref reaches inside the frame)."
        )
    )
    ref: str | None = Field(default=None, description="Element ref from the latest snapshot, for example e12.")
    to_ref: str | None = Field(default=None, description="For drag: the ref of the element to drop onto.")
    text: str | None = Field(
        default=None,
        description=(
            "Text for fill, type and insert. For select: the label or value of one of the options the snapshot lists, "
            "written exactly as listed."
        ),
    )
    key: str | None = Field(default=None, description="Key or chord for press, for example Enter or Control+A.")
    x: float | None = Field(default=None, description="Viewport x in CSS pixels, for click_at and drag_at's start.")
    y: float | None = Field(default=None, description="Viewport y in CSS pixels, for click_at and drag_at's start.")
    to_x: float | None = Field(default=None, description="Viewport x in CSS pixels where a drag_at lets go.")
    to_y: float | None = Field(default=None, description="Viewport y in CSS pixels where a drag_at lets go.")
    expect_text: str | None = Field(
        default=None,
        min_length=EXPECT_MIN_CHARS,
        max_length=EXPECT_MAX_CHARS,
        description=(
            "Exact words the page must show after this step and not before (the line a sent form answers with). "
            "Dot waits for them and says verified or uncertain; unseen, the later steps are not run."
        ),
    )
    expect_url: str | None = Field(
        default=None,
        min_length=2,
        max_length=EXPECT_MAX_CHARS,
        description="Part of the address the tab must come to after this step (/order/done); like expect_text.",
    )

    @model_validator(mode="before")
    @classmethod
    def _plain_meaning(cls, data: object) -> object:
        """A step written as the model meant it, where that is plain (item 755): a click at x and y with no ref is a
        click_at, as in the owner's runs, where it cost the batch; ``upload`` is no step, and says which tool is."""
        if not isinstance(data, dict):
            return data
        action: object = data.get("action")
        if action == "click" and data.get("ref") is None and data.get("x") is not None and data.get("y") is not None:
            return {**data, "action": "click_at"}
        if action == "upload":
            raise ValueError("upload is not a step: call browser_upload with the ref of the upload control and paths")
        if "expect" in data:
            raise ValueError(
                "expect belongs to the call, beside steps, and is a guess in your own words; a step takes expect_text "
                "(exact words the page must show after it) or expect_url (part of the address it must come to)"
            )
        return data

    @model_validator(mode="after")
    def _check_fields(self) -> Self:
        action: str = self.action
        if action in _WRITING_ACTIONS and self.ref is None:
            raise ValueError(
                f"{action} needs a ref: the ref of the field to write in, from browser_find (for example 'textbox') "
                "or the snapshot, a field inside a frame too; a click_at gives the steps after it no field"
            )
        if action in _REF_ACTIONS and self.ref is None:
            raise ValueError(f"{action} needs a ref")
        if action in _TEXT_ACTIONS and self.text is None:
            raise ValueError(f"{action} needs text")
        if action in ("type", "insert") and self.text == "":
            raise ValueError(f"{action} needs text that is not empty; fill with an empty text clears a field")
        if action == "press" and self.key is None:
            raise ValueError("press needs a key")
        if action == "click_at" and (self.x is None or self.y is None or self.ref is not None):
            raise ValueError("click_at needs x and y and no ref")
        if action == "drag_at" and (
            self.x is None
            or self.y is None
            or self.to_x is None
            or self.to_y is None
            or self.ref is not None
            or self.to_ref is not None
        ):
            raise ValueError("drag_at needs x, y, to_x and to_y and no refs")
        if action == "drag" and self.to_ref is None:
            raise ValueError("drag needs a to_ref")
        unused: list[str] = []
        if self.text is not None and action not in _TEXT_ACTIONS:
            unused.append("text")
        if self.key is not None and action != "press":
            unused.append("key")
        if (self.x is not None or self.y is not None) and action not in ("click_at", "drag_at"):
            unused.append("x/y")
        if (self.to_x is not None or self.to_y is not None) and action != "drag_at":
            unused.append("to_x/to_y")
        if self.ref is not None and action not in _REF_ACTIONS and action != "press":
            unused.append("ref")
        if self.to_ref is not None and action != "drag":
            unused.append("to_ref")
        if unused:
            raise ValueError(f"{action} does not use {', '.join(unused)}")
        return self

    def has_side_effects(self) -> bool:
        return self.action in _SIDE_EFFECT_ACTIONS

    def expects(self) -> bool:
        """Whether the step names what the page must show after it (item 1145)."""
        return self.expect_text is not None or self.expect_url is not None

    def acts_on_what_was_left(self) -> bool:
        """Whether the step acts on what the steps before it left, so that it waits when one of their checks is not
        verified (item 1145): every step but one that only writes into a field or moves the view; a text with a line
        break sends what its field holds."""
        if self.action not in _INDEPENDENT_ACTIONS:
            return True
        return self.text is not None and ("\n" in self.text or "\r" in self.text)


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


# How a script run in a page ended (item 263): with a value, with an error it threw, still running when the wait ended,
# or cut off by a navigation of its page.
ScriptEnd = Literal["value", "error", "timeout", "navigated"]


class ScriptRun(FrozenModel):
    """What a script run in the page of a tab did: how it ended, ``text`` (the JSON of its value, or the message of its
    error; empty otherwise), the address of the thread's current tab after it, what else happened (a new tab, a dialog,
    a navigation), and how long it ran."""

    end: ScriptEnd
    text: str
    url: str
    surprise: str | None
    seconds: float
    # The requests other than reads (not GET, HEAD or OPTIONS) the page sent while the script ran and the page settled
    # after it, as method, host and path, each with how many times (item 741): what a script may have sent away.
    sent: tuple[str, ...] = ()


class TextWait(FrozenModel):
    """What a wait for a text found: whether the text became visible in time, how long the wait took (the whole wait
    when it did not appear) and the page after it."""

    appeared: bool
    waited_s: float
    view: PageView
    # What ended a wait the text did not end before its time (item 698): a dialog that opened in front of the page.
    ended_by: str | None = None
    # How long the page had stood still when that ended the wait (item 774), None when it did not.
    still_s: float | None = None


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


# The part of a login a field of a page takes when Dot fills it from the owner's password manager (item 493) or from
# Dot's own vault (item 621).
VaultFieldKind = Literal["username", "password", "otp"]


class VaultFill(FrozenModel):
    """One field of a page to fill from the password manager: its ref in the latest snapshot, what of the login it
    takes, and the value, which no record and no model ever reads (``SecretStr``: its repr and its dump hide it)."""

    ref: str
    kind: VaultFieldKind
    value: SecretStr


@dataclass(frozen=True, slots=True)
class HeldValue:
    """A value Dot filled into a page (item 621) and the documents it may be in: those whose address ``covers`` accepts,
    the origin of the document that holds the field it went into (:class:`dotagent.vault.hosts.SameOrigin`). Whatever
    Dot does to a document by what the value is (keeps it out of the addresses its requests ask for, marks its fields
    again) it does to such documents alone, in the tab the value was typed into (the session holds it by tab), and it
    does not look into any other: a page that was never given the value would otherwise learn it from how Dot acts,
    and a page of the same origin in another tab is not given it when its script is the run's own (``browser_eval``,
    which stays off in the tab the value is in). Two fills of the same value into the same origin are equal."""

    value: str
    covers: Callable[[str], bool]

    def __repr__(self) -> str:
        return "HeldValue(<hidden>)"


class SecretField(FrozenModel):
    """A field that takes a secret, at the moment a step is about to type into it, on the page at ``url``. ``ref`` is
    the ref the step names, ``None`` for a key pressed without one."""

    ref: str | None
    kind: SecretKind
    url: str


# What a step does at a field that takes a secret: it returns to let the text in, and raises to refuse.
type SecretFieldCheck = Callable[[SecretField, str | None], Awaitable[None]]


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


class Frame(FrozenModel):
    """One JPEG of a tab from Chrome's screencast, with the address the tab was on."""

    url: str
    data: bytes
