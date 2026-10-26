#!/usr/bin/env python3
"""A stand-in for ``opencode serve`` (item 677): it answers OpenCode's health check, and at ``/probe`` says what a
process it starts may do in its sandbox. It reads the token file of the Dot home its config folder is in, writes in
its own data folder, writes in its config folder, and connects back to its own port."""

import http.server
import json
import os
import socket
import subprocess
import sys
from pathlib import Path

PORT: int = int(sys.argv[sys.argv.index("--port") + 1])
# <Dot's home>/opencode/sandboxed/config
TOKEN: Path = Path(os.environ["XDG_CONFIG_HOME"]).parents[2] / "api-token"


def _child(code: str) -> bool:
    """Whether a process this one starts, as a command of its agent would be, does what ``code`` does."""
    return subprocess.run([sys.executable, "-c", code], capture_output=True, check=False).returncode == 0


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body: bytes = b"{}"
        if self.path == "/probe":
            data: str = os.path.join(os.environ["XDG_DATA_HOME"], "note")
            config: str = os.path.join(os.environ["XDG_CONFIG_HOME"], "opencode", "note")
            body = json.dumps(
                {
                    "read_sealed": _child(f"open({str(TOKEN)!r}).read()"),
                    "write_data": _child(f"open({data!r}, 'w').write('x')"),
                    "write_config": _child(f"open({config!r}, 'w').write('x')"),
                    "reach_itself": _child(f"import socket; socket.create_connection(('127.0.0.1', {PORT}), 2)"),
                    # An IPv4 address written in IPv6 reaches the IPv4 listener (the security review of item 677).
                    "reach_itself_mapped": _child(
                        f"import socket; s = socket.socket(socket.AF_INET6); s.settimeout(2); "
                        f"s.connect(('::ffff:127.0.0.1', {PORT}))"
                    ),
                }
            ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_: object) -> None:
        return


socket.setdefaulttimeout(5)
http.server.HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
