"""A stand-in for cua-driver (item 2007): the three commands Dot runs, on a socket of its own, with no screen.

- ``manifest`` prints the version in ``FAKE_CUA_VERSION`` (0.28.2 when unset), noting that it ran in ``FAKE_CUA_LOG``.
- ``serve --socket <path>`` listens on that Unix socket and answers what the proxy relays: ``initialize``
  and ``tools/call``. Each call is appended to the JSON-lines file ``FAKE_CUA_LOG`` with the daemon's argv and the
  environment variables Dot sets. ``get_window_state`` answers a window at (100, 100), 400 by 300 points, captured at
  ``FAKE_CUA_SHOT_WIDTH`` pixels wide (800 when unset); a tool named in ``FAKE_CUA_FAIL`` answers an error, and one
  named in ``FAKE_CUA_HANG`` never answers.
- ``mcp --socket <path>`` relays its standard input to the socket and the socket's lines to its standard output.

Usage: ``python fake_cua_driver.py <command> [options]``.
"""

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

_ENV_TOLD: tuple[str, ...] = ("CUA_DRIVER_EMBEDDED", "CUA_DRIVER_RS_TELEMETRY_ENABLED", "CUA_DRIVER_RS_UPDATE_CHECK")


def _option(name: str) -> str:
    return sys.argv[sys.argv.index(name) + 1]


def _answer(request: dict[str, object]) -> dict[str, object] | None:
    method: object = request.get("method")
    if "id" not in request:
        return None
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": request["id"], "result": {"protocolVersion": "2025-06-18", "capabilities": {}}}
    params: object = request.get("params")
    assert isinstance(params, dict)
    tool: str = str(params["name"])
    with Path(os.environ["FAKE_CUA_LOG"]).open("a", encoding="utf-8") as log:
        told: dict[str, str | None] = {name: os.environ.get(name) for name in _ENV_TOLD}
        log.write(
            json.dumps({"tool": tool, "arguments": params["arguments"], "argv": sys.argv[1:], "env": told}) + "\n"
        )
    if tool == os.environ.get("FAKE_CUA_HANG"):
        time.sleep(3600)
    if tool == os.environ.get("FAKE_CUA_FAIL"):
        result: dict[str, object] = {"content": [{"type": "text", "text": f"{tool} failed"}], "isError": True}
    elif tool == "get_window_state":
        result = {
            "content": [{"type": "text", "text": "captured"}],
            "structuredContent": {
                "window_bounds": {"x": 100.0, "y": 100.0, "width": 400.0, "height": 300.0},
                "screenshot_width": int(os.environ.get("FAKE_CUA_SHOT_WIDTH", "800")),
            },
        }
    else:
        result = {
            "content": [{"type": "text", "text": f"did {tool}"}],
            "structuredContent": {"route": "synthetic_events", "effect": "unverifiable"},
        }
    return {"jsonrpc": "2.0", "id": request["id"], "result": result}


def _serve_one(connection: socket.socket) -> None:
    with connection, connection.makefile("rwb") as stream:
        for line in stream:
            answer: dict[str, object] | None = _answer(json.loads(line))
            if answer is not None:
                stream.write(json.dumps(answer).encode() + b"\n")
                stream.flush()


def serve() -> None:
    listener: socket.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(_option("--socket"))
    listener.listen()
    while True:
        connection, _ = listener.accept()
        threading.Thread(target=_serve_one, args=(connection,), daemon=True).start()


def mcp() -> None:
    connection: socket.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.connect(_option("--socket"))
    reader = connection.makefile("rb")

    def relay_out() -> None:
        for line in reader:
            sys.stdout.buffer.write(line)
            sys.stdout.buffer.flush()

    threading.Thread(target=relay_out, daemon=True).start()
    for line in sys.stdin.buffer:
        connection.sendall(line)


def main() -> None:
    match sys.argv[1]:
        case "manifest":
            with Path(os.environ["FAKE_CUA_LOG"]).open("a", encoding="utf-8") as log:
                log.write(json.dumps({"tool": "manifest", "argv": sys.argv[1:]}) + "\n")
            print(json.dumps({"binary_version": os.environ.get("FAKE_CUA_VERSION", "0.28.2")}))
        case "serve":
            serve()
        case "mcp":
            mcp()
        case other:
            raise SystemExit(f"fake cua-driver: no command {other}")


if __name__ == "__main__":
    main()
