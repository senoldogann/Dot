"""Where an application of Customize › Connectors stands, and the step that sets it up (item 278)."""

from typing import Literal

from dotagent.store.models import BrowserMode

type ConnectionState = Literal[
    "allowed",
    "not_asked",
    "refused",
    "not_open",
    "not_installed",
    "signed_in",
    "signed_out",
    "lent",
    "synced",
    "not_synced",
]
# ``ask``: macOS's question; ``settings``: the Automation pane of System Settings, after a no; ``sign_in``: the sign-in
# page in Dot's browser; ``open_app``: the application itself, to sign in there.
type ConnectionStep = Literal["ask", "settings", "sign_in", "open_app"]

_MAC_STEPS: dict[ConnectionState, ConnectionStep] = {"not_asked": "ask", "not_open": "ask", "refused": "settings"}


def mac_state(live: ConnectionState, kept: ConnectionState | None) -> ConnectionState:
    """Where an application of the Mac stands: what macOS says about it now, ``live``, or while it does not run, when
    macOS tells nothing about it, the last answer macOS gave about it, ``kept`` (item 288), so that an application
    allowed once does not ask for Allow again each time it is closed."""
    return kept if live == "not_open" and kept is not None else live


def mac_step(state: ConnectionState) -> ConnectionStep | None:
    """macOS's question for an application it was not asked about (one that does not run is started hidden for it),
    the Automation pane for one it refused; none for one allowed or not installed."""
    return _MAC_STEPS.get(state)


def web_state(mode: BrowserMode, signed: bool) -> ConnectionState:
    """``lent`` while Dot works in the owner's own Chrome, whose sign-ins it uses as they are; else whether the owner
    is signed in in Dot's own browser."""
    if mode == "attach":
        return "lent"
    return "signed_in" if signed else "signed_out"


def web_step(state: ConnectionState, headless: bool) -> ConnectionStep | None:
    """The sign-in page for an application the owner is not signed in to, in a browser with a window to sign in in."""
    return "sign_in" if state == "signed_out" and not headless else None


def folder_state(installed: bool, synced: bool) -> ConnectionState:
    """``synced`` once the application keeps a folder of this Mac in sync; else whether it is installed."""
    if synced:
        return "synced"
    return "not_synced" if installed else "not_installed"


def folder_step(state: ConnectionState) -> ConnectionStep | None:
    """The application itself, to sign in there, while it syncs no folder."""
    return "open_app" if state == "not_synced" else None
