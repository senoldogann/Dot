"""Views of the desktop: which part of an accessibility tree the model is shown, and how much of it changed since last
time.

A first look at a view is complete. A later look at the same view shows only the lines that are new or different, so a
follow-up snapshot after a click costs a few lines instead of the whole window again.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from dotagent.desktop.render import (
    Delta,
    Line,
    diff_lines,
    fit_lines,
    lines_by_ref,
    prefers_delta,
    render_lines,
    search_lines,
)
from dotagent.desktop.types import (
    AppInfo,
    AppView,
    Node,
    Snapshot,
    SnapshotScope,
    SnapshotView,
    WindowInfo,
    WindowView,
)

_MIN_DELTA_NODES: int = 12
_SEARCH_LIMIT: int = 60
_TITLE_CHARS: int = 80


@dataclass(frozen=True, slots=True)
class ViewRequest:
    """Which view of an application is wanted: a scope, or the subtree of one ref, optionally filtered by a query.
    ``full`` asks for the whole view even when only a little changed since the last one; ``max_chars`` is the most text
    of the window it may carry, what one tool result of the run that asks has room for."""

    scope: SnapshotScope
    ref: int | None
    query: str | None
    full: bool
    max_chars: int


@dataclass(frozen=True, slots=True)
class ViewResult:
    """A finished view and the baseline to remember: the text of each line by ref (``None``: keep the stored one)."""

    view: SnapshotView
    baseline: Mapping[int, str] | None


def view_key(request: ViewRequest) -> str:
    """The name a view's baseline is stored under: a subtree view has its own baseline per ref."""
    return f"ref:{request.ref}" if request.ref is not None else request.scope


def _clip(text: str, limit: int) -> str:
    flat: str = " ".join(text.split())
    return flat if len(flat) <= limit else f"{flat[: limit - 1]}…"


def heading_of(request: ViewRequest, nodes: Sequence[Node]) -> str:
    """What the view covers, in words. The title of a window comes from the application."""
    if request.ref is not None:
        return f"a{request.ref} subtree"
    if request.scope == "menu_bar":
        return "menu bar"
    windows: list[Node] = [node for node in nodes if node.role == "AXWindow" and node.indent == 0]
    if request.scope == "all_windows":
        return f"all windows ({len(windows)})"
    title: str = _clip(windows[0].label, _TITLE_CHARS) if windows else ""
    return f'window "{title}"' if title else "window"


def _is_app_window(window: WindowInfo) -> bool:
    """Whether a window of the window list is one a person would call a window: an ordinary one that is on screen or
    has a title. The empty hidden windows every application keeps are menu bar and Dock helpers."""
    return (
        window.layer == 0
        and window.alpha > 0.0
        and window.bounds.width > 1
        and window.bounds.height > 1
        and (window.on_screen or bool(window.title))
    )


def has_window(pid: int, windows: Sequence[WindowInfo]) -> bool:
    return any(window.pid == pid and _is_app_window(window) for window in windows)


def titles_of_windows_shown_by(owners: Sequence[str], windows: Sequence[WindowInfo]) -> tuple[tuple[str, str], ...]:
    """The on-screen windows of the applications named ``owners`` (read without case), each as the application's name
    and the window's title (``""`` for one without a title), in the order the window list has them, front first."""
    wanted: frozenset[str] = frozenset(owner.casefold() for owner in owners)
    return tuple(
        (window.owner, window.title)
        for window in windows
        if window.owner.casefold() in wanted and window.on_screen and _is_app_window(window)
    )


def _window_views(pid: int, windows: Sequence[WindowInfo], scale: float) -> tuple[WindowView, ...]:
    return tuple(
        WindowView(
            title=window.title,
            x=round(window.bounds.x * scale),
            y=round(window.bounds.y * scale),
            width=round(window.bounds.width * scale),
            height=round(window.bounds.height * scale),
            on_screen=window.on_screen,
        )
        for window in windows
        if window.pid == pid and _is_app_window(window)
    )


def app_view(app: AppInfo, windows: Sequence[WindowInfo], scale: float) -> AppView:
    """An application with its windows in model units."""
    return AppView(
        pid=app.pid,
        name=app.name,
        bundle_id=app.bundle_id,
        frontmost=app.frontmost,
        windows=_window_views(app.pid, windows, scale),
    )


def app_views(apps: Sequence[AppInfo], windows: Sequence[WindowInfo], scale: float) -> tuple[AppView, ...]:
    """The applications a person would call open: regular ones, and menu-bar helpers that show a window.
    The one with the keyboard focus comes first."""
    views: list[AppView] = [
        view
        for view in (app_view(app, windows, scale) for app in apps if app.kind != "background")
        if view.windows or view.pid in {app.pid for app in apps if app.kind == "regular"}
    ]
    return tuple(sorted(views, key=lambda view: (not view.frontmost, view.name.casefold())))


def _search_view(
    app: AppView, heading: str, snapshot: Snapshot, query: str, scale: float, notes: tuple[str, ...]
) -> SnapshotView:
    hits, total = search_lines(snapshot.nodes, scale, query, _SEARCH_LIMIT)
    return SnapshotView(
        app=app,
        heading=f'{heading} · matches for "{_clip(query, _TITLE_CHARS)}"',
        mode="full",
        body="\n".join(hits),
        nodes=len(snapshot.nodes),
        shown=len(hits),
        omitted=total - len(hits),
        removed=0,
        truncated=snapshot.truncated,
        notes=notes,
    )


def _delta_for(baseline: Mapping[int, str] | None, lines: tuple[Line, ...]) -> Delta | None:
    """The delta against the stored baseline when the view is big enough and the delta clearly smaller than the
    whole."""
    if baseline is None or len(lines) < _MIN_DELTA_NODES:
        return None
    delta: Delta = diff_lines(baseline, lines)
    return delta if prefers_delta(delta) else None


def build_view(
    app: AppView,
    heading: str,
    snapshot: Snapshot,
    request: ViewRequest,
    baseline: Mapping[int, str] | None,
    scale: float,
    notes: tuple[str, ...],
) -> ViewResult:
    """Turn a walk of the tree into what the model reads: matches of a query, the changes since the stored baseline,
    or the whole view fitted to the request's ``max_chars``."""
    if request.query is not None:
        return ViewResult(_search_view(app, heading, snapshot, request.query, scale, notes), None)
    lines: tuple[Line, ...] = render_lines(snapshot.nodes, scale)
    delta: Delta | None = None if request.full else _delta_for(baseline, lines)
    shown: tuple[Line, ...] = lines if delta is None else delta.changed
    body, omitted = fit_lines(shown, request.max_chars)
    view: SnapshotView = SnapshotView(
        app=app,
        heading=heading,
        mode="full" if delta is None else "delta",
        body=body,
        nodes=len(lines),
        shown=len(shown) - omitted,
        omitted=omitted,
        removed=0 if delta is None else delta.removed,
        truncated=snapshot.truncated,
        notes=notes,
    )
    complete: bool = omitted == 0 and not snapshot.truncated
    return ViewResult(view, lines_by_ref(lines) if complete else None)
