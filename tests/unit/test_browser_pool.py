"""The browser pool (item 32): the daemon's session for its own mode, one session kept per other mode, no Chrome
started by making them."""

from pathlib import Path

from dotagent.browser.pool import BrowserPool
from dotagent.config import load_default_config


def test_the_pool_keeps_one_session_per_mode_and_starts_none(tmp_path: Path) -> None:
    pool: BrowserPool = BrowserPool(load_default_config().browser, load_default_config().server, tmp_path)
    assert pool.main.config.mode == "own"
    assert pool.session_for(None) is pool.main
    assert pool.session_for("own") is pool.main
    attached = pool.session_for("attach")
    assert attached is not pool.main and attached.config.mode == "attach"
    assert pool.session_for("attach") is attached
    assert not pool.main.connected and not attached.connected
