"""The runs at work at the same time in this process, and where each one works (item 301), so that each run knows the
others and leaves what they hold alone.

Every run's tool calls that went through leave a footprint here (:meth:`Peers.saw`): the sites of its pages, the Mac
applications it drives (with its clicks and keys, or with a script that names them), the files it read and the files it
changed. Before a call, :meth:`Peers.conflict` refuses what would step on another run: typing or clicking in an
application another run at work drove within ``APP_HOLD_S``, and changing a file another run changed after this one
last read it (or that this one never read). A call that drives an application or changes a file takes it right before
it runs, after any wait for the user's approval (:meth:`Peers.take`, item 305): the check and the footprint are one
step, so two runs cannot both pass, and a call that fails or is cut off halfway keeps its footprint. A batch of desktop
steps takes each application it moves into the same way (:meth:`Peers.take_app`). An application is one whatever the
case of its name. The tabs need no footprint: each chat has its own and no tool reaches another chat's
(:mod:`dotagent.browser.session`); the browser's idle reaper keeps the tabs of the chats with a run at work, and the
browser is not switched while one is (:meth:`Peers.threads`). A run's loop tells its model who the others are and what
they hold (:func:`peers_note`) whenever that changes. A command line of the shell, a terminal or a script changes the
files the guard reads in it (``ActionInfo.changes``: what it redirects into, removes, copies or moves onto, edits in
place); a folder it removes holds every file in it, and what only the running line knows names none.

A footprint lives as long as its run: :meth:`Peers.begin` when the run starts working, :meth:`Peers.end` when it ends.
The files it changed stay changed for the runs of other chats that were at work with it, so that a write approved
after a long wait does not undo its work unread. Only this process's runs are known; ``dotx run`` in a terminal has
its own.
"""

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from dotagent.policy.types import ActionInfo, ActionKind
from dotagent.tools.apple import APPLE_SCRIPT_TOOL, script_apps

# How long an application another run typed or clicked in stays its own: past that, it went on to other things.
APP_HOLD_S: float = 120.0
_BROWSER_KINDS: frozenset[ActionKind] = frozenset(
    {"browser_read", "browser_navigate", "browser_input", "browser_click"}
)
# The kinds of a desktop tool's or a script's action that drive an application (an observation only reads it).
_DRIVING_KINDS: frozenset[ActionKind] = frozenset({"desktop_input", "app_control", "shell"})
_DESKTOP_PREFIX: str = "desktop_"
_GOAL_CHARS: int = 80
_LISTED: int = 3


@dataclass
class _Footprint:
    run_id: str
    thread_id: str
    goal: str
    # What it touched, each with the last time it did (epoch seconds).
    hosts: dict[str, float] = field(default_factory=dict)
    apps: dict[str, float] = field(default_factory=dict)
    read: dict[str, float] = field(default_factory=dict)
    changed: dict[str, float] = field(default_factory=dict)
    # The files that runs of other chats changed while this one was at work and that have ended since: the time of the
    # change and the goal of the run.
    left: dict[str, tuple[float, str]] = field(default_factory=dict)


@dataclass(frozen=True)
class PeerView:
    """Another run at work as a run's note names it: its goal, its sites, the applications it drives now and the files
    it changed, the latest first."""

    goal: str
    hosts: tuple[str, ...]
    apps: tuple[str, ...]
    changed: tuple[str, ...]


def _latest(touched: Mapping[str, float], since: float) -> tuple[str, ...]:
    return tuple(name for name, _ in sorted(touched.items(), key=lambda item: -item[1]) if touched[name] >= since)


def _driven_apps(tool: str, action: ActionInfo) -> tuple[str, ...]:
    """The applications a call drives: a desktop tool's when it types, clicks or runs something in it, and those an
    AppleScript names as its targets."""
    if action.kind not in _DRIVING_KINDS:
        return ()
    if tool == APPLE_SCRIPT_TOOL:
        return script_apps(action.target)
    return (action.target,) if tool.startswith(_DESKTOP_PREFIX) and action.target != "" else ()


def _touched(apps: Mapping[str, float], app: str, at: float) -> dict[str, float]:
    """``apps`` with ``app`` driven ``at``, under the name it was given last: one application whatever the case."""
    return {**{name: when for name, when in apps.items() if name.casefold() != app.casefold()}, app: at}


def _driven_at(apps: Mapping[str, float], app: str) -> float | None:
    return next((when for name, when in apps.items() if name.casefold() == app.casefold()), None)


def _clock(at: float) -> str:
    return datetime.fromtimestamp(at).astimezone().strftime("%H:%M:%S")


def _short_goal(goal: str) -> str:
    flat: str = " ".join(goal.split())
    return flat if len(flat) <= _GOAL_CHARS else f"{flat[: _GOAL_CHARS - 1]}…"


class Peers:
    """The footprints of the runs at work in this process (module docstring)."""

    def __init__(self) -> None:
        self._runs: dict[str, _Footprint] = {}

    def begin(self, run_id: str, thread_id: str, goal: str) -> None:
        self._runs[run_id] = _Footprint(run_id=run_id, thread_id=thread_id, goal=goal)

    def end(self, run_id: str) -> None:
        """Forget the run; the files it changed stay changed for the runs of other chats that were at work with it."""
        ended: _Footprint | None = self._runs.pop(run_id, None)
        if ended is None:
            return
        for other in self._runs.values():
            if other.thread_id != ended.thread_id:
                other.left.update(
                    (path, (at, ended.goal))
                    for path, at in ended.changed.items()
                    if path not in other.left or other.left[path][0] < at
                )

    def threads(self) -> frozenset[str]:
        """The chats of the runs at work, whose tabs stay open however long they idle."""
        return frozenset(run.thread_id for run in self._runs.values())

    def saw(self, run_id: str, tool: str, action: ActionInfo) -> None:
        """Note what a call of the run that went through touched."""
        mine: _Footprint | None = self._runs.get(run_id)
        if mine is None:
            return
        now: float = time.time()
        if action.kind in _BROWSER_KINDS:
            host: str = urlsplit(action.target).hostname or ""
            if host:
                mine.hosts[host] = now
        elif action.kind == "read":
            mine.read[action.target] = now
        else:
            _claim(mine, tool, action, now)

    def _others(self, run_id: str) -> list[_Footprint]:
        mine: _Footprint | None = self._runs.get(run_id)
        thread: str | None = None if mine is None else mine.thread_id
        return [run for run in self._runs.values() if run.run_id != run_id and run.thread_id != thread]

    def conflict(self, run_id: str, tool: str, action: ActionInfo) -> str | None:
        """Why the run's call would step on another run at work, or ``None`` when it would not (module docstring)."""
        mine: _Footprint | None = self._runs.get(run_id)
        if mine is None:
            return None
        held: str | None = next(filter(None, (self._app_held(mine, app) for app in _driven_apps(tool, action))), None)
        if held is not None:
            return f"Nothing was done: {held}"
        # A script that drives free applications is checked for the files it changes too.
        refused: str = "Nothing was written" if action.kind == "write" else "Nothing was run"
        return next(filter(None, (self._file_changed(mine, path, refused) for path in _changed_paths(action))), None)

    def take(self, run_id: str, tool: str, action: ActionInfo) -> str | None:
        """:meth:`conflict`; when there is none, a call that drives an application or changes a file leaves its
        footprint at once, before it runs (module docstring)."""
        refused: str | None = self.conflict(run_id, tool, action)
        mine: _Footprint | None = self._runs.get(run_id)
        if refused is None and mine is not None:
            _claim(mine, tool, action, time.time())
        return refused

    def take_app(self, run_id: str, app: str) -> str | None:
        """Why ``app`` is another run's now, or ``None`` once this run drives it: a batch of desktop steps checks each
        application it moves into this way."""
        mine: _Footprint | None = self._runs.get(run_id)
        if mine is None:
            return None
        held: str | None = self._app_held(mine, app)
        if held is None:
            mine.apps = _touched(mine.apps, app, time.time())
        return held

    def _app_held(self, mine: _Footprint, app: str) -> str | None:
        now: float = time.time()
        for other in self._others(mine.run_id):
            at: float | None = _driven_at(other.apps, app)
            if at is not None and now - at < APP_HOLD_S:
                return (
                    f"{app} is in use by another run of Dot (“{_short_goal(other.goal)}”), which acted in it"
                    f" {round(now - at)} s ago. Work in another app or on another part of the task for now; an app a"
                    f" run has left alone for {round(APP_HOLD_S)} s is free again."
                )
        return None

    def _file_changed(self, mine: _Footprint, path: str, refused: str) -> str | None:
        """Why changing ``path`` (a file, or a folder a command removes or writes into whole) would undo another run's
        change of it or of a file in it that this run has not read since."""
        for other in self._others(mine.run_id):
            for changed, at in other.changed.items():
                if _within(changed, path) and _unread(mine, changed, at):
                    by: str = f"another run of Dot at work (“{_short_goal(other.goal)}”)"
                    return _unread_change(refused, changed, at, by, mine.read.get(changed))
        for changed, (at, goal) in mine.left.items():
            if _within(changed, path) and _unread(mine, changed, at):
                by = f"another run of Dot (“{_short_goal(goal)}”), ended since"
                return _unread_change(refused, changed, at, by, mine.read.get(changed))
        return None

    def others(self, run_id: str) -> tuple[PeerView, ...]:
        """The other runs at work, as the run's note names them."""
        held_since: float = time.time() - APP_HOLD_S
        return tuple(
            PeerView(
                goal=_short_goal(other.goal),
                hosts=_latest(other.hosts, 0.0)[:_LISTED],
                apps=_latest(other.apps, held_since)[:_LISTED],
                changed=_latest(other.changed, 0.0)[:_LISTED],
            )
            for other in self._others(run_id)
        )


def _changed_paths(action: ActionInfo) -> tuple[str, ...]:
    """The files a call changes: a file tool's file, and those a command line changes or removes (item 305)."""
    return (action.target,) if action.kind == "write" else action.changes


def _within(path: str, root: str) -> bool:
    return path == root or path.startswith(f"{root.rstrip('/')}/")


def _unread(mine: _Footprint, path: str, at: float) -> bool:
    seen: float | None = mine.read.get(path)
    return seen is None or seen < at


def _unread_change(refused: str, path: str, at: float, by: str, seen: float | None) -> str:
    since: str = "you have not read it" if seen is None else f"you last read it at {_clock(seen)}"
    return (
        f"{refused}: {path} was changed at {_clock(at)} by {by}, and {since}. Read it again first, and keep what that"
        " run wrote unless the user asked you to replace it."
    )


def _claim(mine: _Footprint, tool: str, action: ActionInfo, at: float) -> None:
    """Note the applications a call drives and the files it changes ``at``."""
    for app in _driven_apps(tool, action):
        mine.apps = _touched(mine.apps, app, at)
    for path in _changed_paths(action):
        mine.changed[path] = at
        # What it wrote is what it knows of the file.
        mine.read[path] = at


def _place(path: str, workspace: Path) -> str:
    resolved: Path = Path(path)
    return str(resolved.relative_to(workspace)) if resolved.is_relative_to(workspace) else path


def _held(peer: PeerView, workspace: Path) -> str:
    parts: list[str] = []
    if peer.hosts:
        parts.append(f"pages on {', '.join(peer.hosts)}")
    if peer.apps:
        parts.append(f"drives {', '.join(peer.apps)}")
    if peer.changed:
        parts.append(f"changed {', '.join(_place(path, workspace) for path in peer.changed)}")
    return "; ".join(parts) if parts else "nothing on this Mac yet"


def peers_note(peers: tuple[PeerView, ...], workspace: Path) -> str:
    """The note that tells a run about the other runs at work and what they hold; empty when there are none."""
    if not peers:
        return ""
    lines: str = "\n".join(f"- “{peer.goal}”: {_held(peer, workspace)}" for peer in peers)
    return (
        "[system note] Other runs of Dot are at work at the same time on this Mac. Their tabs are out of your reach;"
        " leave the apps they drive, the files they changed and the programs they started to them (typing in such an"
        f" app, or changing such a file before you read it again, is refused):\n{lines}"
    )
