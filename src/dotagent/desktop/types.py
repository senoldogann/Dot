"""Value types of the desktop connector: geometry, windows, applications, accessibility trees and steps.

Geometry is in screen points with the origin at the top-left of the main display (the space of CGEvent, the
accessibility API and the window list). The model never sees points: it sees *model units*, which are points times
the scale of ``model_scale``, so that a full-screen screenshot has exactly one pixel per model unit.
"""

import re
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from dotagent.core.types import FrozenModel

type AXElement = object
"""An opaque ``AXUIElementRef``; only the accessibility module looks inside."""

type Modifier = Literal["cmd", "shift", "alt", "ctrl", "fn"]
type AppKind = Literal["regular", "accessory", "background"]
type ScreenState = Literal["ready", "locked", "display_asleep", "screensaver"]
type SnapshotScope = Literal["window", "all_windows", "menu_bar"]
type StepAction = Literal[
    "click",
    "double_click",
    "right_click",
    "hover",
    "press",
    "focus",
    "set_value",
    "type",
    "paste",
    "key",
    "scroll",
    "drag",
    "menu",
    "wait",
    "activate",
]

DEFAULT_SCROLL_PX: int = 400

_REF: re.Pattern[str] = re.compile(r"^a?(\d{1,7})$")


@dataclass(frozen=True, slots=True)
class Point:
    x: float
    y: float


@dataclass(frozen=True, slots=True)
class Rect:
    x: float
    y: float
    width: float
    height: float

    def center(self) -> Point:
        return Point(self.x + self.width / 2, self.y + self.height / 2)

    def contains(self, point: Point) -> bool:
        return self.x <= point.x < self.x + self.width and self.y <= point.y < self.y + self.height

    def intersects(self, other: "Rect") -> bool:
        return (
            self.x < other.x + other.width
            and other.x < self.x + self.width
            and self.y < other.y + other.height
            and other.y < self.y + self.height
        )

    def union(self, other: "Rect") -> "Rect":
        left: float = min(self.x, other.x)
        top: float = min(self.y, other.y)
        right: float = max(self.x + self.width, other.x + other.width)
        bottom: float = max(self.y + self.height, other.y + other.height)
        return Rect(left, top, right - left, bottom - top)


@dataclass(frozen=True, slots=True)
class DisplayInfo:
    display_id: int
    bounds: Rect
    main: bool


@dataclass(frozen=True, slots=True)
class WindowInfo:
    window_id: int
    pid: int
    owner: str
    title: str
    bounds: Rect
    layer: int
    alpha: float
    on_screen: bool


@dataclass(frozen=True, slots=True)
class AppInfo:
    pid: int
    name: str
    bundle_id: str | None
    bundle_path: str
    kind: AppKind
    frontmost: bool


@dataclass(frozen=True, slots=True)
class Permissions:
    accessibility: bool
    screen_recording: bool
    post_event: bool


@dataclass(frozen=True, slots=True)
class Node:
    """One element of an accessibility tree; ``ref`` is stable across snapshots while the element lives."""

    ref: int
    role: str
    subrole: str
    label: str
    value: str
    frame: Rect | None
    enabled: bool
    focused: bool
    selected: bool
    indent: int

    def secure(self) -> bool:
        return self.subrole == "AXSecureTextField"


@dataclass(frozen=True, slots=True)
class Snapshot:
    """The elements under some roots of one application, in reading order, with the live elements behind the refs."""

    pid: int
    nodes: tuple[Node, ...]
    elements: dict[int, AXElement]
    next_ref: int
    truncated: bool
    elapsed_ms: float
    skipped: dict[int, int]


class Frame(FrozenModel):
    """A screenshot together with the part of the screen it shows."""

    jpeg: bytes
    width: int
    height: int
    region_x: float
    region_y: float
    region_width: float
    region_height: float


class WindowView(FrozenModel):
    title: str
    x: int
    y: int
    width: int
    height: int
    on_screen: bool


class AppView(FrozenModel):
    """An application as the model sees it; ``name`` and the window titles come from the application."""

    pid: int
    name: str
    bundle_id: str | None
    frontmost: bool
    windows: tuple[WindowView, ...]


class SnapshotView(FrozenModel):
    """What the model is shown about an application: a view of its accessibility tree.

    ``heading`` (what the view covers, with the window title), ``body`` and ``notes`` come from the application.
    ``nodes`` counts the elements of the view, ``shown`` the lines of ``body``, ``omitted`` the lines left out to fit the
    budget and ``removed`` the elements of the previous view that are gone (delta views only).
    """

    app: AppView
    heading: str
    mode: Literal["full", "delta"]
    body: str
    nodes: int
    shown: int
    omitted: int
    removed: int
    truncated: bool
    notes: tuple[str, ...]


class ScreenshotView(FrozenModel):
    """A screenshot plus where it sits in model units: pixel (px, py) of the image is the coordinate
    (left + px / zoom, top + py / zoom). ``redacted`` windows (password managers) are blacked out."""

    image: bytes
    width: int
    height: int
    left: int
    top: int
    zoom: float
    pointer_x: int
    pointer_y: int
    frontmost: str
    redacted: int


class StepReport(FrozenModel):
    index: int
    description: str
    outcome: Literal["done", "failed", "skipped"]
    detail: str


class ActOutcome(FrozenModel):
    """The result of a batch: what each step did, why the batch stopped early (if it did) and the window afterwards.
    ``view_note`` says why there is no view (the window is gone, the application quit)."""

    steps: tuple[StepReport, ...]
    stopped: str | None
    view: SnapshotView | None
    view_note: str | None


class OpenedApp(FrozenModel):
    """An application the session opened: where it is, and its window when it already has one."""

    app: AppView
    view: SnapshotView | None
    note: str | None


class TextRead(FrozenModel):
    """The text of one element; ``text`` comes from the application."""

    label: str
    text: str
    total_chars: int
    offset: int


def parse_ref(raw: str) -> int:
    """The numeric part of a ref such as ``a12``; raises ``ValueError`` for anything else."""
    match: re.Match[str] | None = _REF.match(raw.strip().lower())
    if match is None:
        raise ValueError(f"{raw!r} is not an element ref; refs look like a12 and come from desktop_snapshot")
    return int(match.group(1))


_POINTER_ACTIONS: frozenset[str] = frozenset({"click", "double_click", "right_click", "hover"})
_REF_ONLY_ACTIONS: frozenset[str] = frozenset({"press", "focus", "set_value"})
_SIDE_EFFECT_ACTIONS: frozenset[str] = frozenset(
    {
        "click",
        "double_click",
        "right_click",
        "press",
        "focus",
        "set_value",
        "type",
        "paste",
        "key",
        "drag",
        "menu",
        "activate",
    }
)


class Step(BaseModel):
    """One desktop step. Which fields apply depends on ``action``; the others must be left out."""

    model_config = ConfigDict(extra="forbid")

    action: StepAction = Field(
        description=(
            "click | double_click | right_click | hover | press (activate a button or menu item through accessibility, "
            "without moving the pointer) | focus (give a field the keyboard focus) | set_value (replace a field's whole "
            "value at once) | type (key by key into the focused field) | paste (via the clipboard, for long text) | key "
            "(a key or chord such as cmd+s or return) | scroll | drag | menu (choose a menu bar item by its path) | "
            "wait | activate (bring an application to the front)."
        )
    )
    ref: str | None = Field(default=None, description="Element ref from the latest snapshot, for example a12.")
    x: float | None = Field(default=None, description="Screen x in the units of desktop_screenshot, instead of a ref.")
    y: float | None = Field(default=None, description="Screen y in the units of desktop_screenshot, instead of a ref.")
    text: str | None = Field(
        default=None,
        description="Text for type, paste and set_value, or the application (name, bundle id or pid) for activate.",
    )
    key: str | None = Field(
        default=None,
        description="Key or chord for key: cmd+c, cmd+shift+4, return, escape, tab, left, f5, ... (modifiers: cmd, shift, alt, ctrl, fn).",
    )
    modifiers: list[Literal["cmd", "shift", "alt", "ctrl", "fn"]] | None = Field(
        default=None, description='Keys held during a click, for example ["cmd"] to open a link in a new tab.'
    )
    to_ref: str | None = Field(default=None, description="Where drag ends: an element ref.")
    to_x: float | None = Field(default=None, description="Where drag ends: screen x, instead of to_ref.")
    to_y: float | None = Field(default=None, description="Where drag ends: screen y, instead of to_ref.")
    direction: Literal["up", "down", "left", "right"] | None = Field(
        default=None, description="Which way scroll reveals more content."
    )
    amount: int | None = Field(default=None, ge=1, le=20000, description="Scroll distance in pixels (default 400).")
    path: list[str] | None = Field(
        default=None,
        description='Menu path for menu, as shown by desktop_snapshot with scope menu_bar: ["File", "Save"].',
    )
    seconds: float | None = Field(default=None, gt=0, le=10, description="Seconds to pause for wait.")

    @model_validator(mode="after")
    def _check_fields(self) -> Self:
        action: str = self.action
        has_point: bool = self.x is not None and self.y is not None
        if action in _POINTER_ACTIONS and self.ref is None and not has_point:
            raise ValueError(f"{action} needs a ref or both x and y")
        if action in _POINTER_ACTIONS and self.ref is not None and (self.x is not None or self.y is not None):
            raise ValueError(f"{action} takes a ref or x and y, not both")
        if action in _REF_ONLY_ACTIONS and self.ref is None:
            raise ValueError(f"{action} needs a ref")
        if action in ("set_value", "paste", "type", "activate") and not self.text:
            raise ValueError(f"{action} needs text")
        if action == "key" and not self.key:
            raise ValueError("key needs a key")
        if action == "scroll" and self.direction is None:
            raise ValueError("scroll needs a direction")
        if action == "drag":
            if self.ref is None and not has_point:
                raise ValueError("drag needs a start ref or x and y")
            if self.to_ref is None and (self.to_x is None or self.to_y is None):
                raise ValueError("drag needs an end to_ref or to_x and to_y")
        if action == "menu" and not self.path:
            raise ValueError("menu needs a path")
        if action == "wait" and self.seconds is None:
            raise ValueError("wait needs seconds")
        unused: list[str] = []
        if self.text is not None and action not in ("set_value", "paste", "type", "activate"):
            unused.append("text")
        if self.key is not None and action != "key":
            unused.append("key")
        if self.modifiers is not None and action not in _POINTER_ACTIONS:
            unused.append("modifiers")
        if (self.to_ref is not None or self.to_x is not None or self.to_y is not None) and action != "drag":
            unused.append("to_*")
        if self.direction is not None and action != "scroll":
            unused.append("direction")
        if self.amount is not None and action != "scroll":
            unused.append("amount")
        if self.path is not None and action != "menu":
            unused.append("path")
        if self.seconds is not None and action != "wait":
            unused.append("seconds")
        takes_target: bool = (
            action in _POINTER_ACTIONS or action in _REF_ONLY_ACTIONS or action in ("type", "scroll", "drag")
        )
        if (self.ref is not None or self.x is not None or self.y is not None) and not takes_target:
            unused.append("ref/x/y")
        if unused:
            raise ValueError(f"{action} does not use {', '.join(unused)}")
        return self

    def has_side_effects(self) -> bool:
        return self.action in _SIDE_EFFECT_ACTIONS
