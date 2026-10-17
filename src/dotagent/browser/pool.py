"""The browser sessions of the daemon: the one the configuration names, which ``dotx browser attach`` moves between
Dot's own Chrome and the Chrome the user has open, and one for the other mode made on first use, so that a campaign can
work in the user's Chrome while the runs started by hand keep Dot's own, or the other way round.

A session in a mode starts or connects with the first call that needs it; the pool only makes the objects. Closing the
pool closes every session.
"""

from collections.abc import Callable
from contextlib import AsyncExitStack
from pathlib import Path

from dotagent.browser.modes import browser_data_dir
from dotagent.browser.session import BrowserSession
from dotagent.config import BrowserConfig, ServerConfig
from dotagent.store.models import BrowserMode


class BrowserPool:
    def __init__(
        self, config: BrowserConfig, server: ServerConfig, downloads_dir: Path, kept: Callable[[], frozenset[str]]
    ) -> None:
        self._server: ServerConfig = server
        self._downloads: Path = downloads_dir
        # The chats of the runs at work, whose tabs no session's idle reaper closes.
        self._kept: Callable[[], frozenset[str]] = kept
        self.main: BrowserSession = BrowserSession(config, server, browser_data_dir(config.mode), downloads_dir, kept)
        self._others: dict[BrowserMode, BrowserSession] = {}

    def session_for(self, mode: BrowserMode | None) -> BrowserSession:
        """The session for ``mode``: the main one when ``mode`` is ``None`` (the daemon's default) or its mode, else
        the session kept for that mode, made on first use from the main session's configuration. A main session that
        changed modes since leaves the other one as it is."""
        if mode is None or mode == self.main.config.mode:
            return self.main
        found: BrowserSession | None = self._others.get(mode)
        if found is None:
            found = BrowserSession(
                self.main.config.model_copy(update={"mode": mode}),
                self._server,
                browser_data_dir(mode),
                self._downloads,
                self._kept,
            )
            self._others[mode] = found
        return found

    def chrome_pids(self) -> frozenset[int]:
        """The Chromes the sessions work in, by the pid that holds each one's profile: Dot's own Chrome while it runs,
        and the Chrome of the user. A name that fits several Chromes means one of these on the desktop (item 343)."""
        sessions: tuple[BrowserSession, ...] = (self.main, *self._others.values())
        return frozenset(pid for session in sessions if (pid := session.chrome_pid()) is not None)

    async def set_debug_port(self, port: int) -> None:
        """The port Dot's own Chrome takes DevTools connections on (item 265), for every session: the own Chrome of a
        campaign takes it as well. Every session is checked before any changes, so a session that refuses the port
        leaves all of them as they were."""
        sessions: tuple[BrowserSession, ...] = (self.main, *self._others.values())
        for session in sessions:
            await session.check_debug_port(port)
        for session in sessions:
            await session.set_debug_port(port)

    async def aclose(self) -> None:
        """Close every session; one that fails to close does not keep the others open (the error is raised once all
        were tried)."""
        async with AsyncExitStack() as closing:
            closing.push_async_callback(self.main.aclose)
            for session in self._others.values():
                closing.push_async_callback(session.aclose)
