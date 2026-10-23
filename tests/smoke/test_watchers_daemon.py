"""The loop of the watchers runs in the real daemon (item 571): a daemon started on a home that holds a watcher looks
for it at once and says on its row why it could not (the account it names is not signed in, which needs no network),
and one that has ``[watchers] enabled = false`` looks for none. The real command runs in a child process with a
throw-away home, logs and Chrome folder, as the phone's test of the restart does."""

import asyncio
import os
import signal
import socket
import sys
import time
from pathlib import Path

from dotagent.proactive.watchers import WatcherRecord, WatcherRequest, add_watcher, list_watchers
from dotagent.store.db import Store

_RUN_WAIT_S: float = 60.0
_QUIET_S: float = 3.0
# A port is chosen free and the daemon binds it a second or two later, in which another program may take it (a browser
# of a test beside this one connects from the same range of ports): the daemon then says it cannot listen, and the test
# chooses another, as many times as this.
_ATTEMPTS: int = 3


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _serving(port: int) -> bool:
    """Whether something accepts connections on ``port`` of this Mac."""
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def _configure(home: Path, watchers: str) -> int:
    """Write the configuration of ``home``: a free port for the server and the ``[watchers]`` setting given; returns
    the port."""
    port: int = _free_port()
    (home / "config.toml").write_text(f"[server]\nport = {port}\n\n[watchers]\n{watchers}", encoding="utf-8")
    return port


def _new_home(tmp_path: Path) -> Path:
    home: Path = tmp_path / "home"
    home.mkdir()
    return home


async def _home_with_a_watcher(tmp_path: Path) -> Path:
    """A home whose store holds a mail watcher of an account that is not signed in."""
    home: Path = _new_home(tmp_path)
    store: Store = await Store.open(home / "dot.sqlite3", home / "screenshots")
    try:
        request: WatcherRequest = WatcherRequest(
            name="Landlord", kind="mail", account="nobody@example.com", words="the rent", minutes=5
        )
        await add_watcher(store, request, time.time())
    finally:
        await store.close()
    return home


async def _stored(home: Path) -> WatcherRecord:
    store: Store = await Store.open(home / "dot.sqlite3", home / "screenshots")
    try:
        return (await list_watchers(store))[0]
    finally:
        await store.close()


def _said(tmp_path: Path) -> str:
    """What the daemon last wrote to its standard error."""
    return (tmp_path / "daemon.err").read_text(encoding="utf-8", errors="replace")[-2000:]


async def _spawn(tmp_path: Path, home: Path) -> asyncio.subprocess.Process:
    """The real daemon of ``home``, whose standard error is kept in ``tmp_path``."""
    with (tmp_path / "daemon.err").open("wb") as errors:
        return await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "dotagent",
            "daemon",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=errors,
            env={
                **os.environ,
                "DOT_HOME": str(home),
                "DOT_LOG_DIR": str(tmp_path / "logs"),
                "DOT_CHROME_DIR": str(tmp_path / "chrome"),
            },
        )


async def _running(tmp_path: Path, home: Path, watchers: str) -> asyncio.subprocess.Process:
    """The real daemon of ``home`` with the ``[watchers]`` setting given, once it serves. A daemon that ends because its
    port was taken is started again on another; any other end is the test's failure, with what the daemon said."""
    for attempt in range(1, _ATTEMPTS + 1):
        port: int = _configure(home, watchers)
        daemon: asyncio.subprocess.Process = await _spawn(tmp_path, home)
        async with asyncio.timeout(_RUN_WAIT_S):
            while True:
                if daemon.returncode is not None or await asyncio.to_thread(_serving, port):
                    break
                await asyncio.sleep(0.2)
        if daemon.returncode is None:
            return daemon
        assert "cannot listen" in _said(tmp_path) and attempt < _ATTEMPTS, f"the daemon ended: {_said(tmp_path)}"
    raise AssertionError("no daemon was started")


async def _stopped(daemon: asyncio.subprocess.Process) -> int:
    daemon.send_signal(signal.SIGTERM)
    async with asyncio.timeout(_RUN_WAIT_S):
        return await daemon.wait()


async def test_the_daemon_looks_for_a_watcher_and_says_on_its_row_why_it_could_not(tmp_path: Path) -> None:
    home: Path = await _home_with_a_watcher(tmp_path)
    daemon: asyncio.subprocess.Process = await _running(tmp_path, home, "tick_s = 0.2\n")
    try:
        async with asyncio.timeout(_RUN_WAIT_S):
            while (watcher := await _stored(home)).state.error is None:
                assert daemon.returncode is None, f"the daemon ended before it looked: {_said(tmp_path)}"
                await asyncio.sleep(0.3)
        assert "nobody@example.com" in watcher.state.error
        assert watcher.state.next_look_at > time.time(), "it is looked for again later, not at once"
    finally:
        ended: int = await _stopped(daemon)
    assert ended == 0


async def test_a_daemon_with_the_watchers_off_looks_for_none(tmp_path: Path) -> None:
    home: Path = await _home_with_a_watcher(tmp_path)
    daemon: asyncio.subprocess.Process = await _running(tmp_path, home, "enabled = false\ntick_s = 0.2\n")
    try:
        # A watcher due at once would be looked for within a tick of the daemon's start; it serves already.
        await asyncio.sleep(_QUIET_S)
        assert daemon.returncode is None, f"the daemon ended: {_said(tmp_path)}"
        watcher: WatcherRecord = await _stored(home)
        assert watcher.state.error is None and watcher.state.looked_at is None
    finally:
        ended: int = await _stopped(daemon)
    assert ended == 0
