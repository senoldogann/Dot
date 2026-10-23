"""``dotx setup`` with its flags (item 333). The real command runs in a child process, in a Dot home where nothing is
chosen yet, and leaves the service alone (``--no-service``). It:

- checks this Mac;
- stores the key it reads from standard input in Dot's ``.env`` file, and keeps it on the next run;
- checks the model against the list its provider serves to that key;
- writes the model as the main, fast, review and vision model;
- with the models of a provider other than Ollama, switches the notes' embeddings off (item 558) where the Ollama of
  this Mac does not serve ``bge-m3``, and leaves them on where it does.

A model the provider does not list is refused, and no model is written. The provider is a stand-in OpenAI service on
this Mac that lists its models to that key alone; the key is not a real one. Ollama is a port of this Mac nothing
listens on, or a stand-in that lists the embedding model.
"""

import json
import os
import socket
import stat
import subprocess
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

from dotagent.config import AppConfig, ModelsConfig, load_config
from dotagent.secrets import parse_env_file

KEY_NAME: str = "DOT_TEST_SETUP_OPENAI_KEY"
KEY: str = "MARKER-setup-openai-key-0001"
UNSET: str = "DOT_TEST_SURELY_UNSET_SECRET"
MODEL: str = "openai:gpt-5.5"
_TIME_LIMIT_S: float = 60.0


class _OpenAI(BaseHTTPRequestHandler):
    """OpenAI's list of models, served to the key ``KEY`` alone, as OpenAI's own refuses any other (HTTP 401). The
    ``Authorization`` of every request is kept in ``seen``."""

    seen: ClassVar[list[str]] = []

    def do_GET(self) -> None:
        self.seen.append(self.headers.get("Authorization", ""))
        allowed: bool = self.path == "/v1/models" and self.headers.get("Authorization") == f"Bearer {KEY}"
        body: dict[str, object] = (
            {
                "object": "list",
                "data": [{"id": "gpt-5.5", "object": "model"}, {"id": "gpt-5.5-mini", "object": "model"}],
            }
            if allowed
            else {"error": {"message": "Incorrect API key provided", "type": "invalid_request_error"}}
        )
        payload: bytes = json.dumps(body).encode("utf-8")
        self.send_response(200 if allowed else 401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return None


class _Ollama(BaseHTTPRequestHandler):
    """The models Ollama has, as ``/api/tags`` lists them: the embedding model only."""

    def do_GET(self) -> None:
        payload: bytes = json.dumps({"models": [{"name": "bge-m3:latest"}]}).encode("utf-8")
        self.send_response(200 if self.path == "/api/tags" else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return None


def _closed_port() -> int:
    """A port of this Mac that nothing listens on: an Ollama that is not running."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def _ollama() -> Iterator[str]:
    """The stand-in Ollama, on a free port of this Mac, for as long as the block runs."""
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _Ollama)
    thread: threading.Thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@contextmanager
def _openai() -> Iterator[str]:
    """The stand-in OpenAI service, on a free port of this Mac, for as long as the block runs."""
    _OpenAI.seen = []
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _OpenAI)
    thread: threading.Thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _setup(home: Path, logs: Path, arguments: list[str], given: str | None) -> subprocess.CompletedProcess[str]:
    """``dotx setup`` with ``arguments`` in the Dot home ``home``, its standard input ``given`` (none when ``None``)."""
    env: dict[str, str] = {
        **{name: value for name, value in os.environ.items() if name != KEY_NAME},
        "DOT_HOME": str(home),
        "DOT_LOG_DIR": str(logs),
    }
    return subprocess.run(
        [sys.executable, "-m", "dotagent", "setup", *arguments],
        input=given,
        stdin=subprocess.DEVNULL if given is None else None,
        capture_output=True,
        text=True,
        env=env,
        timeout=_TIME_LIMIT_S,
        check=False,
    )


def _config_text(openai: str, ollama: str) -> str:
    """A config file whose OpenAI is the stand-in at ``openai`` and whose Ollama is the server at ``ollama``."""
    return (
        f'[providers.openai]\nbase_url = "{openai}/v1"\napi_key_env = "{KEY_NAME}"\n'
        f'organization_env = "{UNSET}"\nproject_env = "{UNSET}"\n\n'
        f'[providers.ollama]\nbase_url = "{ollama}"\napi_key_env = "{UNSET}"\n'
    )


def test_setup_stores_the_key_and_writes_a_model_its_provider_serves(tmp_path: Path) -> None:
    home: Path = tmp_path / "home"
    home.mkdir()
    config_file: Path = home / "config.toml"
    with _openai() as root:
        config_file.write_text(_config_text(root, f"http://127.0.0.1:{_closed_port()}"), encoding="utf-8")
        before: AppConfig = load_config(config_file)

        refused: subprocess.CompletedProcess[str] = _setup(
            home, tmp_path / "logs", ["--model", "openai:gpt-missing", "--key-stdin", "--no-service"], f"{KEY}\n"
        )
        assert refused.returncode == 2, (refused.stdout, refused.stderr)
        assert "openai does not list gpt-missing" in refused.stderr
        assert load_config(config_file).models == before.models
        env_file: Path = home / ".env"
        assert parse_env_file(env_file.read_text(encoding="utf-8")) == {KEY_NAME: KEY}
        assert stat.S_IMODE(env_file.stat().st_mode) == 0o600

        done: subprocess.CompletedProcess[str] = _setup(
            home, tmp_path / "logs", ["--model", MODEL, "--no-service"], None
        )
        assert done.returncode == 0, (done.stdout, done.stderr)
        assert f"{KEY_NAME} is set and kept" in done.stdout
        assert "dotx open" in done.stdout
        seen: list[str] = list(_OpenAI.seen)
    models: ModelsConfig = load_config(config_file).models
    assert (models.main, models.fast, models.review, models.vision) == (MODEL, MODEL, MODEL, MODEL)
    assert seen and all(header == f"Bearer {KEY}" for header in seen), seen
    # No Ollama answers on this Mac: the notes are found by their words, which setup says (item 558).
    assert not load_config(config_file).memory.embed_enabled
    assert "no Ollama answers on this Mac" in done.stdout and "embed_enabled = false" in done.stdout


def test_setup_leaves_the_embeddings_on_where_ollama_serves_them(tmp_path: Path) -> None:
    home: Path = tmp_path / "home"
    home.mkdir()
    config_file: Path = home / "config.toml"
    with _openai() as openai, _ollama() as ollama:
        config_file.write_text(_config_text(openai, ollama), encoding="utf-8")
        done: subprocess.CompletedProcess[str] = _setup(
            home, tmp_path / "logs", ["--model", MODEL, "--no-service", "--key-stdin"], f"{KEY}\n"
        )
    assert done.returncode == 0, (done.stdout, done.stderr)
    assert load_config(config_file).memory.embed_enabled
    assert "Notes" not in done.stdout and "[memory]" not in config_file.read_text(encoding="utf-8")
