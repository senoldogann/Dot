"""The browser sessions of the daemon: the one the configuration names, which ``dotx browser attach`` moves between
Dot's own Chrome and the Chrome the user has open, and one for the other mode made on first use, so that a campaign can
work in the user's Chrome while the runs started by hand keep Dot's own, or the other way round.

A session in a mode starts or connects with the first call that needs it; the pool only makes the objects. Closing the
pool closes every session.
"""

from collections.abc import Callable
from contextlib import AsyncExitStack
from pathlib import Path

from dotagent.browser.look import PageLook
from dotagent.browser.modes import browser_data_dir
from dotagent.browser.session import BrowserSession
from dotagent.config import BrowserConfig, ServerConfig
from dotagent.core.errors import BrowserError
from dotagent.core.logs import get_logger
from dotagent.store.models import BrowserMode

_log = get_logger("dotagent.browser.pool")


class BrowserPool:
    def __init__(
        self, config: BrowserConfig, server: ServerConfig, downloads_dir: Path, kept: Callable[[], frozenset[str]]
    ) -> None:
        self._server: ServerConfig = server
        self._downloads: Path = downloads_dir
        # The chats of the runs at work, whose tabs no session's idle reaper closes.
        self._kept: Callable[[], frozenset[str]] = kept
        self.main: BrowserSession = BrowserSession(
            config, server, browser_data_dir(config.mode, config.profile), downloads_dir, kept
        )
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
                browser_data_dir(mode, self.main.config.profile),
                self._downloads,
                self._kept,
            )
            self._others[mode] = found
        return found

    def chrome_pids(self) -> frozenset[int]:
        """The Chromes the sessions work in, by the pid that holds each one's profile: Dot's own Chrome while it runs,
        and the Chrome of the user. A name that fits several Chromes means one of these on the desktop (item 343)."""
        sessions: tuple[BrowserSession, ...] = (self.main, *self._others.values())
        pids: set[int] = set()
        for session in sessions:
            try:
                pid: int | None = session.chrome_pid()
            except BrowserError as error:
                # A lock that cannot be read names no Chrome: the name is resolved as if none were Dot's.
                _log.warning("browser_profile_lock_unreadable", profile=str(session.profile_dir), error=error.message)
                continue
            if pid is not None:
                pids.add(pid)
        return frozenset(pids)

    async def set_debug_port(self, port: int) -> None:
        """The port Dot's own Chrome takes DevTools connections on (item 265), for every session: the own Chrome of a
        campaign takes it as well. Every session is checked before any changes, so a session that refuses the port
        leaves all of them as they were."""
        sessions: tuple[BrowserSession, ...] = (self.main, *self._others.values())
        for session in sessions:
            await session.check_debug_port(port)
        for session in sessions:
            await session.set_debug_port(port)

    def set_enabled(self, enabled: bool) -> None:
        """Whether runs are offered the browser tools (item 354), for every session's next runs."""
        for session in (self.main, *self._others.values()):
            session.set_enabled(enabled)

    async def set_look(self, look: PageLook) -> None:
        """How the pages of every session look from their next call on (item 354), as
        :meth:`BrowserSession.set_look` says."""
        for session in (self.main, *self._others.values()):
            await session.set_look(look)

    async def use_profile(self, profile: str) -> None:
        """The profile of Dot's own Chrome every session works on from its next call on (item 355), as
        :meth:`BrowserSession.use_profile` says."""
        for session in (self.main, *self._others.values()):
            await session.use_profile(profile)

    async def aclose(self) -> None:
        """Close every session; one that fails to close does not keep the others open (the error is raised once all
        were tried)."""
        async with AsyncExitStack() as closing:
            closing.push_async_callback(self.main.aclose)
            for session in self._others.values():
                closing.push_async_callback(session.aclose)
