"""The runs at work at the same time in this process, and where each one works (item 301), so that each run knows the
others and leaves what they hold alone.

Every run's tool calls that went through leave a footprint here (:meth:`Peers.saw`): the sites of its pages, the Mac
applications it drives, the files it read and the files it changed. Before a call, :meth:`Peers.conflict` refuses what
would step on another run at work: typing or clicking in an application another run drove within ``APP_HOLD_S``, and
changing a file another run changed after this one last read it (or that this one never read). The tabs need no
footprint: each chat has its own and no tool reaches another chat's (:mod:`dotagent.browser.session`); the browser's
idle reaper keeps the tabs of the chats with a run at work (:meth:`Peers.threads`). A run's loop tells its
model who the others are and what they hold (:func:`peers_note`) whenever that changes. What a shell command changes
leaves no footprint: it names no file the way the file tools do.

A footprint lives as long as its run: :meth:`Peers.begin` when the run starts working, :meth:`Peers.end` when it ends.
Only this process's runs are known; ``dotx run`` in a terminal has its own.
"""

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from dotagent.policy.types import ActionInfo, ActionKind

# How long an application another run typed or clicked in stays its own: past that, it went on to other things.
APP_HOLD_S: float = 120.0
_BROWSER_KINDS: frozenset[ActionKind] = frozenset(
    {"browser_read", "browser_navigate", "browser_input", "browser_click"}
)
# The kinds of a desktop tool's action that drive its application (an observation only reads it).
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


def _drives(tool: str, action: ActionInfo) -> bool:
    return tool.startswith(_DESKTOP_PREFIX) and action.kind in _DRIVING_KINDS and action.target != ""


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
        self._runs.pop(run_id, None)

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
        elif _drives(tool, action):
            mine.apps[action.target] = now
        elif action.kind == "read":
            mine.read[action.target] = now
        elif action.kind == "write":
            mine.changed[action.target] = now
            # What it wrote is what it knows of the file.
            mine.read[action.target] = now

    def _others(self, run_id: str) -> list[_Footprint]:
        mine: _Footprint | None = self._runs.get(run_id)
        thread: str | None = None if mine is None else mine.thread_id
        return [run for run in self._runs.values() if run.run_id != run_id and run.thread_id != thread]

    def conflict(self, run_id: str, tool: str, action: ActionInfo) -> str | None:
        """Why the run's call would step on another run at work, or ``None`` when it would not (module docstring)."""
        mine: _Footprint | None = self._runs.get(run_id)
        if mine is None:
            return None
        if _drives(tool, action):
            return self._app_held(mine, action.target)
        if action.kind == "write":
            return self._file_changed(mine, action.target)
        return None

    def _app_held(self, mine: _Footprint, app: str) -> str | None:
        now: float = time.time()
        for other in self._others(mine.run_id):
            at: float | None = other.apps.get(app)
            if at is not None and now - at < APP_HOLD_S:
                return (
                    f"Nothing was done: {app} is in use by another run of Dot (“{_short_goal(other.goal)}”), which acted"
                    f" in it {round(now - at)} s ago. Work in another app or on another part of the task for now; an"
                    f" app a run has left alone for {round(APP_HOLD_S)} s is free again."
                )
        return None

    def _file_changed(self, mine: _Footprint, path: str) -> str | None:
        seen: float | None = mine.read.get(path)
        for other in self._others(mine.run_id):
            at: float | None = other.changed.get(path)
            if at is not None and (seen is None or seen < at):
                since: str = "you have not read it" if seen is None else f"you last read it at {_clock(seen)}"
                return (
                    f"Nothing was written: {path} was changed at {_clock(at)} by another run of Dot at work"
                    f" (“{_short_goal(other.goal)}”), and {since}. Read it again first, and keep what that run"
                    " wrote unless the user asked you to replace it."
                )
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
        " leave the apps they drive and the files they changed to them (typing in such an app, or changing such a file"
        f" before you read it again, is refused):\n{lines}"
    )
