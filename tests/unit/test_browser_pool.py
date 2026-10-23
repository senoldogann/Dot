"""The browser pool (item 32): the daemon's session for its own mode, one session kept per other mode, no Chrome
started by making them; a DevTools port one session refuses changes none of them (item 265)."""

import socket
from pathlib import Path

import pytest

from dotagent.browser.pool import BrowserPool
from dotagent.browser.session import BrowserSession, no_runs_at_work
from dotagent.config import load_default_config
from dotagent.core.errors import BrowserError


def test_the_pool_keeps_one_session_per_mode_and_starts_none(tmp_path: Path) -> None:
    pool: BrowserPool = BrowserPool(
        load_default_config().browser, load_default_config().server, tmp_path, no_runs_at_work
    )
    assert pool.main.config.mode == "own"
    assert pool.session_for(None) is pool.main
    assert pool.session_for("own") is pool.main
    attached = pool.session_for("attach")
    assert attached is not pool.main and attached.config.mode == "attach"
    assert pool.session_for("attach") is attached
    assert not pool.main.connected and not attached.connected


async def test_the_session_kept_for_the_mode_the_main_one_switches_into_is_closed_and_dropped(tmp_path: Path) -> None:
    """Item 554: a session a campaign made for a mode, left in the pool when the main session switched into that mode,
    would keep its Chrome on the profile the main session starts on, with no way to close it."""
    pool: BrowserPool = BrowserPool(
        load_default_config().browser, load_default_config().server, tmp_path, no_runs_at_work
    )
    attached: BrowserSession = pool.session_for("attach")
    await pool.retire_other("own")
    assert pool.session_for("attach") is attached
    await pool.retire_other("attach")
    with pytest.raises(BrowserError, match="the browser session was closed"):
        await attached.let_go_of_chrome()
    fresh: BrowserSession = pool.session_for("attach")
    assert fresh is not attached and fresh.config.mode == "attach"
    await pool.aclose()


async def test_a_port_one_session_refuses_leaves_every_session_as_it_was(tmp_path: Path) -> None:
    pool: BrowserPool = BrowserPool(
        load_default_config().browser, load_default_config().server, tmp_path, no_runs_at_work
    )
    attached: BrowserSession = pool.session_for("attach")
    await attached.aclose()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = int(probe.getsockname()[1])
    try:
        with pytest.raises(BrowserError, match="the browser session was closed"):
            await pool.set_debug_port(port)
        assert (pool.main.config.debug_port, attached.config.debug_port) == (0, 0)
    finally:
        await pool.aclose()
