"""A start of OpenCode's server that hangs (item 1092): one start on 2026-11-09 read its config and then answered
nothing for the 60 s Dot waits. The server is started afresh once; a second start that hangs too fails the run with
what the process was doing and what it wrote. A stand-in program plays OpenCode: nothing here reaches outside this
Mac."""

import asyncio
import sys
from pathlib import Path

import pytest

from dotagent.config import OpenCodeConfig
from dotagent.core.errors import OpenCodeStartTimedOut
from dotagent.opencode.server import OpenCodeServer

pytestmark = pytest.mark.asyncio

# The stand-in: each start counted in a file; the starts up to ``hangs`` write a line and answer nothing, as the hung
# start did, and the next one serves the health and the tool list.
# The stand-in: each start counted in a file; the starts up to ``hangs`` write a line and answer nothing, as the hung
# start did, and the next one answers every request with 200 (the health, and an empty list of tools). A bare socket
# and no site packages (``-IS``), so that it answers in a moment on a loaded runner (``http.server`` and its imports
# took longer than the start's deadline there).
_STAND_IN: str = """#!{python} -IS
import os, socket, sys, time
count = {count!r}
starts = int(open(count).read()) + 1 if os.path.exists(count) else 1
open(count, "w").write(str(starts))
print("loading config", flush=True)
if starts <= {hangs}:
    time.sleep(600)
port = int(sys.argv[sys.argv.index("--port") + 1])
server = socket.socket()
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("127.0.0.1", port))
server.listen(8)
while True:
    client, _ = server.accept()
    asked = b""
    while b"\\r\\n\\r\\n" not in asked:
        part = client.recv(4096)
        if not part:
            break
        asked += part
    body = b'{{"healthy": true}}' if b" /global/health" in asked.split(b"\\r\\n", 1)[0] else b"[]"
    head = "HTTP/1.1 200 OK\\r\\nContent-Type: application/json\\r\\n"
    head += "Content-Length: %d\\r\\nConnection: close\\r\\n\\r\\n"
    client.sendall((head % len(body)).encode() + body)
    client.close()
"""


def _stand_in(tmp_path: Path, hangs: int, monkeypatch: pytest.MonkeyPatch) -> Path:
    count: Path = tmp_path / "starts"
    program: Path = tmp_path / "opencode"
    program.write_text(_STAND_IN.format(python=sys.executable, count=str(count), hangs=hangs), encoding="utf-8")
    program.chmod(0o755)
    monkeypatch.setenv("DOT_OPENCODE", str(program))
    monkeypatch.setenv("DOT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DOT_LOG_DIR", str(tmp_path / "logs"))
    (tmp_path / "logs").mkdir()
    return count


# How long a start that answers may take: room for a runner loaded far past its cores (load 30 on the gate), as the
# first start of the test waits it out whole. A start of the test that both hang never answers, so it waits less.
_ANSWERING_START_S: float = 30.0
_HANGING_START_S: float = 3.0


def _server(start_timeout_s: float) -> OpenCodeServer:
    config: OpenCodeConfig = OpenCodeConfig(
        command="opencode", start_timeout_s=start_timeout_s, idle_stop_s=600.0, models_ttl_s=60.0
    )
    return OpenCodeServer(config, {"PATH": "/usr/bin:/bin"})


async def test_a_start_that_hangs_is_started_afresh_and_the_run_goes_on(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    count: Path = _stand_in(tmp_path, 1, monkeypatch)
    server: OpenCodeServer = _server(_ANSWERING_START_S)
    try:
        assert await server.tool_ids(tmp_path) == ()
    finally:
        await server.aclose()
    assert await asyncio.to_thread(count.read_text) == "2"


async def test_a_second_start_that_hangs_fails_with_what_the_process_did_and_wrote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    count: Path = _stand_in(tmp_path, 2, monkeypatch)
    server: OpenCodeServer = _server(_HANGING_START_S)
    try:
        with pytest.raises(OpenCodeStartTimedOut) as raised:
            await server.tool_ids(tmp_path)
    finally:
        await server.aclose()
    assert await asyncio.to_thread(count.read_text) == "2"
    context = raised.value.context
    # A process asleep (S), not one at work: the line ps gives, and the line the program wrote before it hung.
    assert str(context["process"]).startswith("S"), context
    assert "loading config" in str(context["output"])
    assert len(str(context["load"]).split()) == 3
