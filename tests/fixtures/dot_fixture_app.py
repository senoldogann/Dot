"""A small AppKit application that the desktop smoke tests drive.

It opens one window with a button, a checkbox, a text field, a secure field, a scroll view and a pad that reacts to the
mouse. Every event it receives is appended to a JSON-lines log, so a test can compare what a tool did with what the
application actually saw. Usage: ``python dot_fixture_app.py <log path>``.
"""

import json
import sys
import time
from pathlib import Path

import objc
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyRegular,
    NSBackingStoreBuffered,
    NSBezelStyleRounded,
    NSButton,
    NSButtonTypeSwitch,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSMenu,
    NSMenuItem,
    NSRectFill,
    NSScrollView,
    NSSecureTextField,
    NSString,
    NSTextField,
    NSTrackingActiveAlways,
    NSTrackingArea,
    NSTrackingInVisibleRect,
    NSTrackingMouseEnteredAndExited,
    NSTrackingMouseMoved,
    NSView,
    NSViewBoundsDidChangeNotification,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
)
from Foundation import NSMakePoint, NSMakeRect, NSNotificationCenter, NSObject, NSProcessInfo

_LOG_PATH: Path = Path(sys.argv[1])
_GRID_STEP: float = 100.0
_GRID_WIDTH: float = 1600.0
_GRID_HEIGHT: float = 2400.0
_MOVE_LOG_GAP_S: float = 0.05
_state: dict[str, float] = {"last_move": 0.0}


def log_event(kind: str, **fields: object) -> None:
    """Append one event to the log; the file is opened per event so a reader never sees a half-written line."""
    line: str = json.dumps({"event": kind, "t": round(time.time(), 3), **fields}, ensure_ascii=False)
    with _LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def where(view: NSView, event: object) -> tuple[float, float]:
    """The position of a mouse event in the coordinates of ``view`` (top-left origin for the flipped views here)."""
    point = view.convertPoint_fromView_(event.locationInWindow(), None)
    return round(float(point.x), 1), round(float(point.y), 1)


class GridView(NSView):
    """A tall and wide document with a labelled grid, so a scroll shows up in screenshots too."""

    def isFlipped(self) -> bool:
        return True

    def drawRect_(self, rect: object) -> None:
        NSColor.whiteColor().setFill()
        NSRectFill(self.bounds())
        attributes = {
            NSFontAttributeName: NSFont.systemFontOfSize_(12),
            NSForegroundColorAttributeName: NSColor.darkGrayColor(),
        }
        rows: int = int(_GRID_HEIGHT // _GRID_STEP)
        columns: int = int(_GRID_WIDTH // _GRID_STEP)
        for row in range(rows):
            for column in range(columns):
                NSString.stringWithString_(f"r{row} c{column}").drawAtPoint_withAttributes_(
                    NSMakePoint(column * _GRID_STEP + 6, row * _GRID_STEP + 6), attributes
                )


class LoggingScrollView(NSScrollView):
    """A scroll view that reports the wheel events it receives."""

    def scrollWheel_(self, event: object) -> None:
        log_event(
            "wheel",
            dx=float(event.scrollingDeltaX()),
            dy=float(event.scrollingDeltaY()),
            precise=bool(event.hasPreciseScrollingDeltas()),
        )
        objc.super(LoggingScrollView, self).scrollWheel_(event)


class PadView(NSView):
    """A surface that reports clicks, drags, hovering, the context menu and key presses."""

    def isFlipped(self) -> bool:
        return True

    def acceptsFirstResponder(self) -> bool:
        return True

    def acceptsFirstMouse_(self, event: object) -> bool:
        return True

    def drawRect_(self, rect: object) -> None:
        NSColor.colorWithCalibratedRed_green_blue_alpha_(0.85, 0.92, 1.0, 1.0).setFill()
        NSRectFill(self.bounds())

    def updateTrackingAreas(self) -> None:
        objc.super(PadView, self).updateTrackingAreas()
        for area in list(self.trackingAreas()):
            self.removeTrackingArea_(area)
        options: int = (
            NSTrackingMouseMoved | NSTrackingMouseEnteredAndExited | NSTrackingActiveAlways | NSTrackingInVisibleRect
        )
        self.addTrackingArea_(
            NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(self.bounds(), options, self, None)
        )

    def mouseDown_(self, event: object) -> None:
        self.window().makeFirstResponder_(self)
        x, y = where(self, event)
        log_event("pad_down", x=x, y=y, clicks=int(event.clickCount()), modifiers=int(event.modifierFlags()))

    def mouseDragged_(self, event: object) -> None:
        x, y = where(self, event)
        log_event("pad_drag", x=x, y=y)

    def mouseUp_(self, event: object) -> None:
        x, y = where(self, event)
        log_event("pad_up", x=x, y=y, clicks=int(event.clickCount()))

    def mouseMoved_(self, event: object) -> None:
        now: float = time.monotonic()
        if now - _state["last_move"] < _MOVE_LOG_GAP_S:
            return
        _state["last_move"] = now
        x, y = where(self, event)
        log_event("pad_move", x=x, y=y)

    def mouseEntered_(self, event: object) -> None:
        log_event("pad_enter")

    def mouseExited_(self, event: object) -> None:
        log_event("pad_exit")

    def keyDown_(self, event: object) -> None:
        log_event("key", chars=str(event.characters()), code=int(event.keyCode()), modifiers=int(event.modifierFlags()))

    def menuForEvent_(self, event: object) -> object:
        x, y = where(self, event)
        log_event("pad_context", x=x, y=y)
        menu = NSMenu.alloc().initWithTitle_("Pad")
        for title in ("Alpha", "Beta"):
            item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, "contextPicked:", "")
            item.setTarget_(self.window().delegate())
            menu.addItem_(item)
        return menu


class FixtureDelegate(NSObject):
    """Owns the controls and turns their actions into log events."""

    def init(self) -> "FixtureDelegate":
        ready: FixtureDelegate = objc.super(FixtureDelegate, self).init()
        ready.count = 0
        ready.counter_label = None
        return ready

    def incrementPressed_(self, sender: object) -> None:
        self.count += 1
        self.counter_label.setStringValue_(f"Counter: {self.count}")
        log_event("button", name="increment", count=self.count)

    def checkboxToggled_(self, sender: object) -> None:
        log_event("checkbox", name="enabled", on=bool(sender.state()))

    def contextPicked_(self, sender: object) -> None:
        log_event("context_pick", item=str(sender.title()))

    def pingChosen_(self, sender: object) -> None:
        log_event("menu", item="Ping")

    def controlTextDidChange_(self, notification: object) -> None:
        field = notification.object()
        secure: bool = bool(field.isKindOfClass_(NSSecureTextField))
        if secure:
            log_event("text", field="secret", length=len(str(field.stringValue())))
        else:
            log_event("text", field="name", value=str(field.stringValue()))

    def scrolled_(self, notification: object) -> None:
        origin = notification.object().bounds().origin
        log_event("scrolled", x=round(float(origin.x), 1), y=round(float(origin.y), 1))

    def windowDidBecomeKey_(self, notification: object) -> None:
        log_event("window_key")

    def applicationShouldTerminateAfterLastWindowClosed_(self, application: object) -> bool:
        return True

    def applicationDidFinishLaunching_(self, notification: object) -> None:
        window = build_window(self)
        window.makeKeyAndOrderFront_(None)
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        frame = window.frame()
        log_event(
            "ready",
            pid=int(NSProcessInfo.processInfo().processIdentifier()),
            window=[round(float(v), 1) for v in (frame.origin.x, frame.origin.y, frame.size.width, frame.size.height)],
        )


def label(text: str, frame: tuple[float, float, float, float]) -> NSTextField:
    field = NSTextField.alloc().initWithFrame_(NSMakeRect(*frame))
    field.setStringValue_(text)
    field.setEditable_(False)
    field.setBezeled_(False)
    field.setDrawsBackground_(False)
    field.setSelectable_(False)
    return field


def build_window(delegate: FixtureDelegate) -> NSWindow:
    style: int = (
        NSWindowStyleMaskTitled
        | NSWindowStyleMaskClosable
        | NSWindowStyleMaskMiniaturizable
        | NSWindowStyleMaskResizable
    )
    window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, 640, 520), style, NSBackingStoreBuffered, False
    )
    window.setTitle_("Dot Fixture")
    window.setDelegate_(delegate)
    window.setReleasedWhenClosed_(False)
    window.center()
    content = window.contentView()

    delegate.counter_label = label("Counter: 0", (20, 480, 200, 22))
    content.addSubview_(delegate.counter_label)

    button = NSButton.alloc().initWithFrame_(NSMakeRect(20, 440, 140, 30))
    button.setTitle_("Increment")
    button.setBezelStyle_(NSBezelStyleRounded)
    button.setTarget_(delegate)
    button.setAction_("incrementPressed:")
    content.addSubview_(button)

    checkbox = NSButton.alloc().initWithFrame_(NSMakeRect(180, 442, 140, 26))
    checkbox.setButtonType_(NSButtonTypeSwitch)
    checkbox.setTitle_("Enabled")
    checkbox.setTarget_(delegate)
    checkbox.setAction_("checkboxToggled:")
    content.addSubview_(checkbox)

    name_field = NSTextField.alloc().initWithFrame_(NSMakeRect(20, 400, 300, 24))
    name_field.setPlaceholderString_("Name")
    name_field.setDelegate_(delegate)
    name_field.setAccessibilityLabel_("Name")
    content.addSubview_(name_field)

    secret_field = NSSecureTextField.alloc().initWithFrame_(NSMakeRect(340, 400, 280, 24))
    secret_field.setPlaceholderString_("Password")
    secret_field.setDelegate_(delegate)
    content.addSubview_(secret_field)

    pad = PadView.alloc().initWithFrame_(NSMakeRect(20, 280, 300, 100))
    pad.setAccessibilityElement_(True)
    pad.setAccessibilityRole_("AXGroup")
    pad.setAccessibilityLabel_("Pad")
    content.addSubview_(pad)

    scroll = LoggingScrollView.alloc().initWithFrame_(NSMakeRect(20, 20, 600, 240))
    scroll.setHasVerticalScroller_(True)
    scroll.setHasHorizontalScroller_(True)
    scroll.setAccessibilityLabel_("Grid scroller")
    grid = GridView.alloc().initWithFrame_(NSMakeRect(0, 0, _GRID_WIDTH, _GRID_HEIGHT))
    scroll.setDocumentView_(grid)
    scroll.contentView().setPostsBoundsChangedNotifications_(True)
    NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
        delegate, "scrolled:", NSViewBoundsDidChangeNotification, scroll.contentView()
    )
    content.addSubview_(scroll)
    return window


def build_menu(delegate: FixtureDelegate) -> NSMenu:
    menubar = NSMenu.alloc().init()
    app_item = NSMenuItem.alloc().init()
    menubar.addItem_(app_item)
    app_menu = NSMenu.alloc().init()
    app_menu.addItemWithTitle_action_keyEquivalent_("Quit Dot Fixture", "terminate:", "q")
    app_item.setSubmenu_(app_menu)
    edit_item = NSMenuItem.alloc().init()
    menubar.addItem_(edit_item)
    edit_menu = NSMenu.alloc().initWithTitle_("Edit")
    for title, selector, key in (
        ("Cut", "cut:", "x"),
        ("Copy", "copy:", "c"),
        ("Paste", "paste:", "v"),
        ("Select All", "selectAll:", "a"),
    ):
        edit_menu.addItemWithTitle_action_keyEquivalent_(title, selector, key)
    edit_item.setSubmenu_(edit_menu)
    actions_item = NSMenuItem.alloc().init()
    menubar.addItem_(actions_item)
    actions_menu = NSMenu.alloc().initWithTitle_("Actions")
    ping = actions_menu.addItemWithTitle_action_keyEquivalent_("Ping", "pingChosen:", "p")
    ping.setTarget_(delegate)
    actions_item.setSubmenu_(actions_menu)
    return menubar


def main() -> None:
    NSProcessInfo.processInfo().setProcessName_("DotFixture")
    application = NSApplication.sharedApplication()
    application.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    delegate = FixtureDelegate.alloc().init()
    application.setDelegate_(delegate)
    application.setMainMenu_(build_menu(delegate))
    application.run()


main()
