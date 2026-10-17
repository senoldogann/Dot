"""The doctor rows that look at this Mac for real: the commands and secrets of MCP servers, and whether macOS can capture
the screen. Nothing is started and nothing is mocked: the login shell, PATH, ``.env`` file, Keychain and ScreenCaptureKit
are all asked. The model rows ask a stand-in model service on this Mac through the real connectors, or an address of this
Mac where nothing listens."""

import asyncio
import json
import platform
import socket
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from dotagent.config import AppConfig, McpServerConfig, load_config
from dotagent.core.errors import ProviderTransportError
from dotagent.desktop.capture import rect_capture_supported
from dotagent.doctor import CheckResult, _service_checks, mcp_checks
from dotagent.llm.ollama import OllamaProvider

LOGIN_TIMEOUT_S: float = 15.0
# Time enough for checks that ask no server, and less than the retries of one call to a server out of reach (15 s at
# the least, with Ollama's six attempts).
AT_ONCE_S: float = 10.0
FIRST_MACOS_WITH_RECT_CAPTURE: tuple[int, int] = (15, 2)
UNSET_SECRET: str = "DOT_TEST_SURELY_UNSET_SECRET"
OPENAI_KEY_ENV: str = "DOT_TEST_DOCTOR_OPENAI_KEY"
GO_KEY_ENV: str = "DOT_TEST_DOCTOR_GO_KEY"
OPENROUTER_KEY_ENV: str = "DOT_TEST_DOCTOR_OPENROUTER_KEY"
# The models the stand-in service lists as OpenAI's.
SERVED: tuple[str, ...] = ("gpt-5.5", "gpt-5.5-mini")


async def test_without_servers_the_mcp_row_says_where_to_add_them(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    (row,) = await mcp_checks([], LOGIN_TIMEOUT_S)
    assert (row.name, row.ok, row.required) == ("mcp", True, False)
    assert "[[mcp]]" in row.detail


@pytest.fixture
def local(tmp_path: Path) -> Path:
    """An executable ``bin/server`` in the workspace of a throwaway Dot home, where a relative command starts from."""
    server: Path = tmp_path / "workspace" / "bin" / "server"
    server.parent.mkdir(parents=True)
    server.write_text("#!/bin/sh\n")
    server.chmod(0o755)
    return server


async def test_each_server_gets_a_row_that_names_what_is_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, local: Path
) -> None:
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    monkeypatch.delenv(UNSET_SECRET, raising=False)
    servers: list[McpServerConfig] = [
        McpServerConfig(name="absolute", command=sys.executable),
        McpServerConfig(name="onpath", command="ls"),
        McpServerConfig(name="relative", command="bin/server"),
        McpServerConfig(name="ghost", command="dot-no-such-server-binary"),
        McpServerConfig(name="pathghost", command="/no/such/dir/server"),
        McpServerConfig(name="needy", command="ls", env={"TOKEN": f"${{{UNSET_SECRET}}}"}),
        McpServerConfig(name="sleeping", command="dot-no-such-server-binary", enabled=False),
    ]
    rows: list[CheckResult] = await mcp_checks(servers, LOGIN_TIMEOUT_S)
    assert [row.name for row in rows] == [f"mcp {server.name}" for server in servers]
    absolute, onpath, relative, ghost, pathghost, needy, sleeping = rows

    assert (absolute.ok, absolute.required) == (True, False)
    assert absolute.detail.startswith(sys.executable)
    assert (onpath.ok, onpath.required) == (True, False)
    assert onpath.detail.split(";")[0].endswith("/ls")
    assert (relative.ok, relative.required) == (True, False)
    assert relative.detail.startswith(str(local))

    assert (ghost.ok, ghost.required) == (False, False)
    assert "dot-no-such-server-binary is not installed" in ghost.detail
    assert "Dot starts without its tools while it fails" in ghost.detail
    assert (pathghost.ok, pathghost.required) == (False, False)
    assert "/no/such/dir/server is not installed" in pathghost.detail

    assert (needy.ok, needy.required) == (False, False)
    assert UNSET_SECRET in needy.detail
    assert "dotx auth set" in needy.detail
    assert "not installed" not in needy.detail

    assert (sleeping.ok, sleeping.required) == (True, False)
    assert "switched off" in sleeping.detail


def test_the_screen_capture_check_follows_the_macos_version() -> None:
    numbers: list[int] = [int(part) for part in platform.mac_ver()[0].split(".")]
    assert rect_capture_supported() is (tuple(numbers[:2]) >= FIRST_MACOS_WITH_RECT_CAPTURE)


class _ModelsHandler(BaseHTTPRequestHandler):
    """A model service whose ``/v1/models`` lists the models of ``SERVED``, as OpenAI's does; any other path, the list
    of OpenCode Go among them, answers 404."""

    def do_GET(self) -> None:
        listed: bool = self.path == "/v1/models"
        body: str = json.dumps(
            {"object": "list", "data": [{"id": model, "object": "model"} for model in SERVED]}
            if listed
            else {"error": {"message": "no such route", "type": "invalid_request_error"}}
        )
        payload: bytes = body.encode("utf-8")
        self.send_response(200 if listed else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return None


@contextmanager
def _model_service() -> Iterator[str]:
    """The stand-in model service, on a free port of this Mac, for as long as the block runs."""
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _ModelsHandler)
    thread: threading.Thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _models_config(root: str) -> str:
    """Models of OpenAI and OpenCode Go alone, whose services are the stand-in at ``root``; every other key is one no
    source has, so nothing else is asked."""
    return (
        '[models]\nmain = "openai:gpt-missing"\nvision = "openai:gpt-5.5"\nfast = "openai:gpt-5.5-mini"\n'
        'review = "opencode_go:kimi-k2"\nhelpers = ["openai:gpt-missing", "openai:gpt-5.5"]\n'
        'standby = ["opencode_go:kimi-k2"]\n\n'
        f'[providers.ollama]\nbase_url = "{root}"\napi_key_env = "{UNSET_SECRET}"\n\n'
        f'[providers.openai]\nbase_url = "{root}/v1"\napi_key_env = "{OPENAI_KEY_ENV}"\n'
        f'organization_env = "{UNSET_SECRET}"\nproject_env = "{UNSET_SECRET}"\n\n'
        f'[providers.opencode_go]\nbase_url = "{root}/go"\napi_key_env = "{GO_KEY_ENV}"\n\n'
        f'[providers.anthropic]\napi_key_env = "{UNSET_SECRET}"\n\n'
        f'[judge]\napi_key_env = "{UNSET_SECRET}"\n\n'
        "[memory]\nembed_enabled = false\n"
    )


async def test_a_model_its_provider_does_not_serve_fails_and_one_whose_list_was_not_read_warns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Item 186: a model of OpenAI or OpenCode Go is checked against the list of the models its provider serves to the
    key. One the list leaves out fails, and is no ready helper; one whose list could not be read is a warning, beside
    the provider's own row."""
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    monkeypatch.setenv(OPENAI_KEY_ENV, "sk-test-doctor")
    monkeypatch.setenv(GO_KEY_ENV, "go-test-doctor")
    monkeypatch.delenv(UNSET_SECRET, raising=False)
    with _model_service() as root:
        (tmp_path / "config.toml").write_text(_models_config(root), encoding="utf-8")
        rows: dict[str, CheckResult] = {
            row.name: row for row in await _service_checks(load_config(tmp_path / "config.toml"))
        }
    assert (rows["openai api"].ok, rows["openai api"].detail) == (True, "2 chat models are available to this key")
    main, vision, fast, review = (rows[f"model {role}"] for role in ("main", "vision", "fast", "review"))
    assert (main.ok, main.required) == (False, True)
    assert main.detail == (
        "openai:gpt-missing: not among the 2 models openai serves to this key (dotx models openai lists them)"
    )
    assert (vision.ok, fast.ok) == (True, True)
    assert (review.ok, review.required) == (False, False)
    assert review.detail.startswith(
        "opencode_go:kimi-k2: not checked, as the list of the models opencode_go serves could not be read (provider "
        "answered HTTP 404"
    )
    assert (rows["helpers"].ok, rows["helpers"].required) == (True, False)
    assert rows["helpers"].detail == f"{main.detail}; openai:gpt-5.5 ready"
    assert (rows["standby"].ok, rows["standby"].required, rows["standby"].detail) == (False, False, review.detail)


class _OpenRouterHandler(BaseHTTPRequestHandler):
    """OpenRouter's list of its models, which lists one, and the page of the key; any other path answers 404."""

    def do_GET(self) -> None:
        answers: dict[str, object] = {
            "/openrouter/models": {
                "data": [
                    {
                        "id": "lab/agent-model",
                        "context_length": 128_000,
                        "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
                        "supported_parameters": ["tools"],
                    }
                ]
            },
            "/openrouter/key": {"data": {"usage": 0.5, "limit": None, "is_free_tier": False}},
        }
        found: object | None = answers.get(self.path)
        payload: bytes = json.dumps(found if found is not None else {"error": {"message": "no such route"}}).encode()
        self.send_response(200 if found is not None else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        return None


@contextmanager
def _openrouter_service() -> Iterator[str]:
    """The stand-in for OpenRouter, on a free port of this Mac, for as long as the block runs."""
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _OpenRouterHandler)
    thread: threading.Thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _openrouter_config(root: str, model: str) -> str:
    """Every role on one OpenRouter model, whose service is the stand-in at ``root``: it has no OpenRouter routes, so
    the list of OpenRouter's models and its key both answer 404. Every other key is one no source has."""
    roles: str = "".join(f'{role} = "{model}"\n' for role in ("main", "vision", "fast", "review"))
    return (
        f"[models]\n{roles}\n"
        f'[providers.ollama]\nbase_url = "{root}"\napi_key_env = "{UNSET_SECRET}"\n\n'
        f'[providers.openai]\napi_key_env = "{UNSET_SECRET}"\norganization_env = "{UNSET_SECRET}"\n'
        f'project_env = "{UNSET_SECRET}"\n\n'
        f'[providers.opencode_go]\napi_key_env = "{UNSET_SECRET}"\n\n'
        f'[providers.anthropic]\napi_key_env = "{UNSET_SECRET}"\n\n'
        f'[providers.openrouter]\nbase_url = "{root}/openrouter"\napi_key_env = "{OPENROUTER_KEY_ENV}"\n\n'
        f'[judge]\napi_key_env = "{UNSET_SECRET}"\n\n'
        "[memory]\nembed_enabled = false\n"
    )


async def test_an_openrouter_model_whose_list_was_not_read_warns_and_is_not_asked_about(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Item 349: OpenRouter's connector knows a model from the list of its models, so a model whose list could not be
    read is the warning beside the provider's row, not a failure of its own from reading that list again."""
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    monkeypatch.setenv(OPENROUTER_KEY_ENV, "sk-or-test-doctor")
    monkeypatch.delenv(UNSET_SECRET, raising=False)
    model: str = "openrouter:lab/agent-model"
    with _model_service() as root:
        (tmp_path / "config.toml").write_text(_openrouter_config(root, model), encoding="utf-8")
        rows: dict[str, CheckResult] = {
            row.name: row for row in await _service_checks(load_config(tmp_path / "config.toml"))
        }
    assert (rows["openrouter api"].ok, rows["openrouter api"].required) == (False, True)
    main: CheckResult = rows["model main"]
    assert (main.ok, main.required) == (False, False)
    assert main.detail.startswith(
        f"{model}: not checked, as the list of the models openrouter serves could not be read (provider answered "
        "HTTP 404"
    )


async def test_a_variant_of_a_model_openrouter_lists_is_served(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Item 349: OpenRouter routes a variant id (``:nitro``) to the model it lists, so the doctor takes it as served, as
    the connector does; it was not among the models OpenRouter serves."""
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    monkeypatch.setenv(OPENROUTER_KEY_ENV, "sk-or-test-doctor")
    monkeypatch.delenv(UNSET_SECRET, raising=False)
    model: str = "openrouter:lab/agent-model:nitro"
    with _openrouter_service() as root:
        (tmp_path / "config.toml").write_text(_openrouter_config(root, model), encoding="utf-8")
        rows: dict[str, CheckResult] = {
            row.name: row for row in await _service_checks(load_config(tmp_path / "config.toml"))
        }
    assert rows["openrouter api"].ok, rows["openrouter api"].detail
    main: CheckResult = rows["model main"]
    assert (main.ok, main.detail) == (True, f"{model} (tools)")


def _closed_port() -> int:
    """A port of this Mac where nothing listens: one the system gives out, freed again."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


async def test_an_ollama_server_that_is_not_running_is_reported_at_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Item 212: with no Ollama server at its address on this Mac, the list of its models (the menu of models asks for
    it) fails at once, and so does the doctor: the server's row fails, the memory row says the embedding models cannot
    be listed, and each Ollama model is a warning that was not asked, instead of each call waiting out its retries."""
    monkeypatch.setenv("DOT_HOME", str(tmp_path))
    monkeypatch.delenv(UNSET_SECRET, raising=False)
    root: str = f"http://127.0.0.1:{_closed_port()}"
    roles: dict[str, str] = {
        "main": "ollama:qwen3",
        "vision": "ollama:qwen3",
        "fast": "ollama:qwen3",
        "review": "ollama:gemma3",
    }
    (tmp_path / "config.toml").write_text(
        "[models]\n"
        + "".join(f'{role} = "{reference}"\n' for role, reference in roles.items())
        + 'helpers = ["ollama:gemma3"]\n\n'
        f'[providers.ollama]\nbase_url = "{root}"\napi_key_env = "{UNSET_SECRET}"\n\n'
        f'[providers.openai]\napi_key_env = "{UNSET_SECRET}"\n\n'
        f'[providers.opencode_go]\napi_key_env = "{UNSET_SECRET}"\n\n'
        f'[providers.anthropic]\napi_key_env = "{UNSET_SECRET}"\n\n'
        f'[judge]\napi_key_env = "{UNSET_SECRET}"\n',
        encoding="utf-8",
    )
    config: AppConfig = load_config(tmp_path / "config.toml")
    provider: OllamaProvider = OllamaProvider(config.providers.ollama, None)
    async with asyncio.timeout(AT_ONCE_S):
        try:
            with pytest.raises(ProviderTransportError):
                await provider.list_models()
        finally:
            await provider.aclose()
        rows: dict[str, CheckResult] = {row.name: row for row in await _service_checks(config)}
    server: CheckResult = rows["ollama server"]
    assert (server.ok, server.required) == (False, True)
    assert server.detail == f"{root}/api/version is not reachable (ConnectError); start Ollama"
    memory: CheckResult = rows["memory"]
    assert (memory.ok, memory.required, memory.detail) == (
        False,
        True,
        f"the embedding models cannot be listed, as {server.detail}",
    )
    for role, reference in roles.items():
        row: CheckResult = rows[f"model {role}"]
        assert (row.ok, row.required, row.detail) == (False, False, f"{reference}: not checked, as {server.detail}")
    assert (rows["helpers"].ok, rows["helpers"].detail) == (False, f"ollama:gemma3: not checked, as {server.detail}")
