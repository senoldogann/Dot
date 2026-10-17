"""OpenCode's own agent as Dot starts it (item 283): ``opencode serve`` on a free loopback port, behind a password Dot
makes, and the part of its HTTP API a run on one of Zen's free models uses. Zen's free models serve OpenCode's own agent
only (403 ``FreeTierError`` to any other client, and to OpenCode itself when a prompt switches its tools off, measured
2026-10-15), so the agent is what a run drives.

The server keeps to a home of its own in Dot's (:func:`dotagent.core.paths.opencode_dir`): its config, data, state and
cache folders and the home where it looks for a ``.opencode`` folder are there, and it reads no project's config, no
external plugin or skill and nothing of Claude Code's, so that none of the owner's own OpenCode setup comes in: no key,
no plugin, no tool of theirs that would act without asking, so that its agent has OpenCode's own tools alone. Its
environment is the login shell's PATH and the few variables a program expects, never the keys of the login environment;
HOME stays the owner's, for the commands its agent runs. Every tool of its agent asks Dot before it acts, and Dot's own
policy answers (:mod:`dotagent.opencode.run`).

The server starts when a run needs it (about 2 s) and stops once it has served nothing for
``[providers.opencode] idle_stop_s`` (it holds about 340 MB while up), and with the daemon. The list of free models
comes from OpenCode's command line instead, in the same home and environment, its catalog refreshed first.
"""

import asyncio
import json
import os
import re
import secrets
import shutil
import socket
import time
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Literal

import httpx
from pydantic import JsonValue

from dotagent.config import OpenCodeConfig
from dotagent.core.errors import JsonParseError, OpenCodeError, OpenCodeMissing
from dotagent.core.jsonutil import parse_json_object, parse_json_value
from dotagent.core.logs import get_logger
from dotagent.core.paths import log_dir, opencode_command, opencode_dir
from dotagent.core.types import FrozenModel, JsonObject
from dotagent.llm.types import Effort
from dotagent.llm.wire import get_int, get_obj, get_str

log = get_logger(__name__)
# How much of an event line that cannot be read its error keeps.
_EVENT_LINE_CHARS: int = 300

# The provider of OpenCode's own catalog that serves Zen's models, free ones among them.
ZEN_PROVIDER: str = "opencode"
# The line ``opencode models <provider>`` prints for each of the provider's models, before its JSON with ``--verbose``.
_CATALOG_ENTRY: re.Pattern[str] = re.compile(rf"^{re.escape(ZEN_PROVIDER)}/(\S+)$", re.MULTILINE)
_USER: str = "opencode"
_HOST: str = "127.0.0.1"
# Where OpenCode is found besides the login shell's PATH: Homebrew on Apple silicon and on Intel, and OpenCode's own
# installer.
_PLACES: tuple[str, ...] = ("/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".opencode" / "bin"))
# The variables of the login environment OpenCode's agent gets: what a program and its commands expect, no key.
_PASSED: tuple[str, ...] = ("HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TERM")
_POLL_S: float = 0.25
_IDLE_CHECK_S: float = 30.0
_STOP_GRACE_S: float = 10.0
_REQUEST_TIMEOUT_S: float = 60.0
_CONNECT_TIMEOUT_S: float = 10.0
# The free models whose providers may use what is sent to them to improve or train their models, by the start of their
# ids (opencode.ai/docs/zen, read 2026-10-15: Big Pickle, Exo and MiMo "collected data may be used to improve the
# model", Muse Spark Contributor for training); docs/models.md names them.
LEARNING_MODELS: tuple[str, ...] = ("big-pickle", "exo-", "mimo-", "muse-spark-")
# What OpenCode's agent may do (``OPENCODE_PERMISSION``, which OpenCode applies over every config): each of its tools
# that acts or reads asks Dot first, and so does a subagent, which Dot refuses (its session would not be the run's). No
# tool is switched off: the tools its agent sends the model stay OpenCode's own, as Zen's free tier wants them (it has
# been reported to refuse OpenCode itself with a tool off, anomalyco/opencode#49433 and #51315, read 2026-10-15);
# questions to the owner stay off as OpenCode has them for its command line.
PERMISSIONS: JsonObject = {
    "read": "ask",
    "glob": "ask",
    "grep": "ask",
    "list": "ask",
    "edit": "ask",
    "bash": "ask",
    "webfetch": "ask",
    "websearch": "ask",
    "codesearch": "ask",
    "external_directory": "ask",
    "doom_loop": "ask",
    "task": "ask",
}
_EFFORTS: tuple[Effort, ...] = ("minimal", "low", "medium", "high", "xhigh", "max")

NOT_INSTALLED: str = (
    "OpenCode is not installed: Zen's free models run as OpenCode's own agent, so install it (brew install opencode,"
    " or see opencode.ai) and try again"
)

type PermissionReply = Literal["once", "reject"]


class FreeModel(FrozenModel):
    """One of Zen's free models as OpenCode describes it: whether it takes pictures, calls tools and thinks, its window,
    and its reasoning levels (OpenCode's variants that are Dot's levels too), lowest first."""

    id: str
    name: str
    vision: bool
    tools: bool
    thinking: bool
    context_length: int | None
    efforts: tuple[Effort, ...]


class _ModelList(FrozenModel):
    fetched_at: float
    models: tuple[FreeModel, ...]


class PromptFile(FrozenModel):
    """A file that goes with a prompt: OpenCode reads it from ``path`` and gives it to the model as its kind allows."""

    path: Path
    media_type: str
    name: str


@dataclass
class _Live:
    process: asyncio.subprocess.Process
    client: httpx.AsyncClient
    log: IO[str]
    # When it started (monotonic): it knows the models of the catalog it read then.
    started_at: float


def find_program(command: str, login_path: str) -> Path | None:
    """The ``opencode`` program ``command`` names: a path, or a name on the login shell's PATH or in the usual
    places; ``None`` when OpenCode is not installed."""
    if os.sep in command:
        candidate: Path = Path(command).expanduser()
        return candidate if os.access(candidate, os.X_OK) else None
    found: str | None = shutil.which(command, path=os.pathsep.join([login_path, *_PLACES]))
    return None if found is None else Path(found)


def _flag(obj: JsonObject, key: str) -> bool:
    return obj.get(key) is True


def _costs_nothing(cost: JsonObject) -> bool:
    """Whether a model's price is zero for what it reads and what it writes; a price that is missing is not zero."""
    prices: list[JsonValue] = [cost.get("input"), cost.get("output")]
    return all(isinstance(price, int | float) and not isinstance(price, bool) and price == 0 for price in prices)


def _catalog_entries(listing: str) -> list[tuple[str, str]]:
    """The models of ``opencode models <provider> --verbose``'s output, which prints each one's ``provider/model`` line
    and then its JSON: each one's id with the text of its JSON."""
    entries: list[re.Match[str]] = list(_CATALOG_ENTRY.finditer(listing))
    ends: list[int] = [entry.start() for entry in entries[1:]] + [len(listing)]
    return [(entry.group(1), listing[entry.end() : end]) for entry, end in zip(entries, ends, strict=True)]


def _free_model(model_id: str, raw: JsonObject) -> FreeModel | None:
    """A model of Zen's as Dot offers it, or ``None`` when it costs something or is deprecated."""
    if not _costs_nothing(get_obj(raw, "cost")) or get_str(raw, "status") == "deprecated":
        return None
    capabilities: JsonObject = get_obj(raw, "capabilities")
    context: int = get_int(get_obj(raw, "limit"), "context")
    variants: JsonObject = get_obj(raw, "variants")
    return FreeModel(
        id=model_id,
        name=get_str(raw, "name") or model_id,
        vision=_flag(get_obj(capabilities, "input"), "image"),
        tools=_flag(capabilities, "toolcall"),
        thinking=_flag(capabilities, "reasoning"),
        context_length=context if context > 0 else None,
        efforts=tuple(effort for effort in _EFFORTS if effort in variants),
    )


def free_models(listing: str) -> tuple[FreeModel, ...]:
    """The free models of Zen in OpenCode's catalog as ``opencode models opencode --verbose`` prints it: those that cost
    nothing and are not deprecated, by id."""
    models: list[FreeModel | None] = [
        _free_model(model_id, parse_json_object(body, f"opencode models: {model_id}"))
        for model_id, body in _catalog_entries(listing)
    ]
    return tuple(sorted((model for model in models if model is not None), key=lambda model: model.id))


def learns_from_data(model: str) -> bool:
    """Whether the provider of the free model ``model`` may learn from what is sent to it."""
    return model.startswith(LEARNING_MODELS)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind((_HOST, 0))
        port: int = sock.getsockname()[1]
    return port


def _managed_config() -> JsonObject:
    """The config of the server Dot starts: no update and no sharing; its permissions come by the environment."""
    return {"$schema": "https://opencode.ai/config.json", "autoupdate": False, "share": "disabled"}


async def _prepared_home() -> Path:
    """The server's home in Dot's (:func:`dotagent.core.paths.opencode_dir`), its folders made and its config
    written."""
    home: Path = opencode_dir()
    for name in ("config", "data", "state", "cache", "home"):
        await asyncio.to_thread((home / name).mkdir, mode=0o700, parents=True, exist_ok=True)
    await asyncio.to_thread(
        (home / "opencode.json").write_text, json.dumps(_managed_config(), indent=2) + "\n", encoding="utf-8"
    )
    return home


def server_environment(login_env: Mapping[str, str], home: Path) -> dict[str, str]:
    """The environment of the server Dot starts, without its password: the login shell's PATH and the variables a
    program expects, and OpenCode's folders and switches that keep the owner's own setup out (module docstring)."""
    env: dict[str, str] = {name: login_env[name] for name in _PASSED if name in login_env}
    env.update(
        {
            "PATH": os.pathsep.join([login_env.get("PATH", "/usr/bin:/bin"), *_PLACES]),
            "XDG_CONFIG_HOME": str(home / "config"),
            "XDG_DATA_HOME": str(home / "data"),
            "XDG_STATE_HOME": str(home / "state"),
            "XDG_CACHE_HOME": str(home / "cache"),
            # Where OpenCode looks for the home's ``.opencode`` folder; the one variable that moves it, HOME staying.
            "OPENCODE_TEST_HOME": str(home / "home"),
            "OPENCODE_CONFIG": str(home / "opencode.json"),
            "OPENCODE_PERMISSION": json.dumps(PERMISSIONS),
            "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
            "OPENCODE_DISABLE_CLAUDE_CODE": "1",
            "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1",
            "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
            "OPENCODE_PURE": "1",
            "OPENCODE_SERVER_USERNAME": _USER,
        }
    )
    return env


class OpenCodeServer:
    """The OpenCode server Dot starts, started on first need and stopped when idle; a connector to the program."""

    def __init__(self, config: OpenCodeConfig, login_env: Mapping[str, str]) -> None:
        self._config: OpenCodeConfig = config
        self._login_env: dict[str, str] = dict(login_env)
        self._lock: asyncio.Lock = asyncio.Lock()
        self._live: _Live | None = None
        self._holds: int = 0
        self._last_used: float = time.monotonic()
        self._idle: asyncio.Task[None] | None = None
        # When the catalog was last refreshed (monotonic), by :meth:`_catalog`; ``None`` before.
        self._catalog_at: float | None = None

    def program(self) -> Path | None:
        return find_program(opencode_command(self._config.command), self._login_env.get("PATH", ""))

    async def _start(self) -> _Live:
        program: Path | None = self.program()
        if program is None:
            raise OpenCodeMissing(NOT_INSTALLED, {"command": opencode_command(self._config.command)})
        home: Path = await _prepared_home()
        password: str = secrets.token_urlsafe(24)
        port: int = _free_port()
        started_at: float = time.monotonic()
        env: dict[str, str] = {**server_environment(self._login_env, home), "OPENCODE_SERVER_PASSWORD": password}
        log_path: Path = log_dir() / "opencode.log"
        output: IO[str] = await asyncio.to_thread(log_path.open, "w", encoding="utf-8")
        process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
            str(program),
            "serve",
            "--hostname",
            _HOST,
            "--port",
            str(port),
            cwd=str(home),
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=output,
            stderr=output,
        )
        client: httpx.AsyncClient = httpx.AsyncClient(
            base_url=f"http://{_HOST}:{port}", auth=(_USER, password), timeout=_REQUEST_TIMEOUT_S
        )
        live: _Live = _Live(process=process, client=client, log=output, started_at=started_at)
        deadline: float = time.monotonic() + self._config.start_timeout_s
        while True:
            if process.returncode is not None:
                await self._release(live)
                raise OpenCodeError(
                    "OpenCode's server stopped while starting; its log says why",
                    {"program": str(program), "exit": process.returncode, "log": str(log_path)},
                )
            with suppress(httpx.TransportError):
                if (await client.get("/global/health")).status_code == 200:
                    break
            if time.monotonic() > deadline:
                await self._release(live)
                raise OpenCodeError(
                    "OpenCode's server did not start in time",
                    {"program": str(program), "start_timeout_s": self._config.start_timeout_s, "log": str(log_path)},
                )
            await asyncio.sleep(_POLL_S)
        log.info("opencode_server_started", program=str(program), pid=process.pid)
        return live

    async def _release(self, live: _Live) -> None:
        if live.process.returncode is None:
            live.process.terminate()
            try:
                async with asyncio.timeout(_STOP_GRACE_S):
                    await live.process.wait()
            except TimeoutError:
                live.process.kill()
                await live.process.wait()
        await live.client.aclose()
        await asyncio.to_thread(live.log.close)

    async def _client(self) -> httpx.AsyncClient:
        async with self._lock:
            self._last_used = time.monotonic()
            current: _Live | None = self._live
            if current is not None and current.process.returncode is None:
                return current.client
            if current is not None:
                log.warning("opencode_server_died", exit=current.process.returncode)
                await self._release(current)
                self._live = None
            started: _Live = await self._start()
            self._live = started
            if self._idle is None or self._idle.done():
                self._idle = asyncio.create_task(self._stop_when_idle(), name="opencode-idle")
            return started.client

    async def _stop_when_idle(self) -> None:
        while True:
            await asyncio.sleep(_IDLE_CHECK_S)
            async with self._lock:
                if self._live is None:
                    return
                if self._holds == 0 and time.monotonic() - self._last_used > self._config.idle_stop_s:
                    log.info("opencode_server_idle_stop", idle_s=round(time.monotonic() - self._last_used))
                    await self._release(self._live)
                    self._live = None
                    return

    async def _drop_outdated(self) -> None:
        """Stop the server when it started before the catalog was last refreshed and serves no run, so that the next one
        starts on the refreshed catalog: a server keeps the catalog it read as it started (measured 2026-10-15, a model
        put back in its catalog's file stayed out of its list), and would not know a model Zen has just added."""
        async with self._lock:
            live: _Live | None = self._live
            if live is None or self._holds > 0 or self._catalog_at is None or live.started_at >= self._catalog_at:
                return
            log.info("opencode_server_stopped_for_catalog", pid=live.process.pid)
            await self._release(live)
            self._live = None

    @asynccontextmanager
    async def hold(self) -> AsyncIterator[None]:
        """Keep the server up while a run uses it, one that knows the models listed (:meth:`_drop_outdated`)."""
        await self._drop_outdated()
        self._holds += 1
        try:
            yield
        finally:
            self._holds -= 1
            self._last_used = time.monotonic()

    async def _request(self, method: str, path: str, directory: Path, body: JsonObject | None) -> httpx.Response:
        client: httpx.AsyncClient = await self._client()
        try:
            return await client.request(method, path, params={"directory": str(directory)}, json=body)
        except httpx.HTTPError as error:
            raise OpenCodeError(
                "OpenCode's server could not be reached",
                {"method": method, "path": path, "error": f"{type(error).__name__}: {error}"},
            ) from error

    async def _send(self, method: str, path: str, directory: Path, body: JsonObject | None) -> httpx.Response:
        response: httpx.Response = await self._request(method, path, directory, body)
        if response.status_code >= 400:
            raise OpenCodeError(
                "OpenCode's server refused the request",
                {"method": method, "path": path, "status": response.status_code, "body": response.text[:2000]},
            )
        return response

    async def list_free_models(self) -> tuple[FreeModel, ...]:
        """Zen's free models, from the list kept in Dot's home while it is younger than ``models_ttl_s``, else from
        OpenCode's catalog refreshed from models.dev first (:meth:`_catalog`). None is offered while OpenCode is not
        installed."""
        program: Path | None = self.program()
        if program is None:
            raise OpenCodeMissing(NOT_INSTALLED, {"command": opencode_command(self._config.command)})
        path: Path = opencode_dir() / "models.json"
        kept: _ModelList | None = await asyncio.to_thread(_read_models, path)
        if kept is not None and time.time() - kept.fetched_at < self._config.models_ttl_s:
            return kept.models
        models: tuple[FreeModel, ...] = free_models(await self._catalog(program))
        self._catalog_at = time.monotonic()
        await asyncio.to_thread(_write_models, path, _ModelList(fetched_at=time.time(), models=models))
        return models

    async def _catalog(self, program: Path) -> str:
        """Zen's models in OpenCode's catalog, refreshed from models.dev before they are listed: ``opencode models
        opencode --refresh --verbose`` in the server's home and environment. A server answers from the catalog it read
        as it started and refreshes it only after (measured 2026-10-15: its list lacked three free models that its
        refreshed catalog, written a second earlier, had), and a server started later reads the refreshed one."""
        home: Path = await _prepared_home()
        process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
            str(program),
            "models",
            ZEN_PROVIDER,
            "--refresh",
            "--verbose",
            cwd=str(home),
            env=server_environment(self._login_env, home),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(process.communicate(), timeout=self._config.start_timeout_s)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise OpenCodeError(
                "OpenCode did not list its models in time", {"timeout_s": self._config.start_timeout_s}
            ) from None
        if process.returncode != 0:
            raise OpenCodeError(
                "OpenCode did not list its models",
                {"exit": process.returncode, "stderr": err.decode("utf-8", errors="replace")[-2000:]},
            )
        return out.decode("utf-8", errors="replace")

    async def tool_ids(self, directory: Path) -> tuple[str, ...]:
        """The tools OpenCode's agent has in ``directory``: its own, and any that another source added."""
        response: httpx.Response = await self._send("GET", "/experimental/tool/ids", directory, None)
        listed: JsonValue = parse_json_value(response.text, "opencode /experimental/tool/ids")
        return tuple(item for item in listed if isinstance(item, str)) if isinstance(listed, list) else ()

    async def create_session(self, directory: Path, title: str) -> str:
        response: httpx.Response = await self._send("POST", "/session", directory, {"title": title})
        session_id: str = get_str(parse_json_object(response.text, "opencode /session"), "id")
        if session_id == "":
            raise OpenCodeError("OpenCode did not open a session", {"directory": str(directory), "body": response.text})
        return session_id

    async def has_session(self, session_id: str, directory: Path) -> bool:
        response: httpx.Response = await self._request("GET", f"/session/{session_id}", directory, None)
        if response.status_code == 404:
            return False
        if response.status_code >= 400:
            raise OpenCodeError(
                "OpenCode's server refused the request",
                {"path": f"/session/{session_id}", "status": response.status_code, "body": response.text[:2000]},
            )
        return True

    async def prompt(
        self,
        session_id: str,
        directory: Path,
        model: str,
        variant: str | None,
        text: str,
        files: tuple[PromptFile, ...],
    ) -> None:
        """Hand ``text`` and ``files`` to the agent of the session on ``model``; it works on while the call returns. A
        prompt sent while the agent works is read once its step ends."""
        parts: list[JsonValue] = [{"type": "text", "text": text}]
        parts.extend(
            {"type": "file", "mime": file.media_type, "filename": file.name, "url": file.path.as_uri()}
            for file in files
        )
        body: JsonObject = {"model": {"providerID": ZEN_PROVIDER, "modelID": model}, "parts": parts}
        if variant is not None:
            body["variant"] = variant
        await self._send("POST", f"/session/{session_id}/prompt_async", directory, body)

    async def abort(self, session_id: str, directory: Path) -> None:
        await self._send("POST", f"/session/{session_id}/abort", directory, None)

    async def reply(self, request_id: str, directory: Path, reply: PermissionReply, message: str | None) -> None:
        """Answer a permission OpenCode's agent asked for; ``message`` tells the agent why it was refused, and it goes
        on with that instead of stopping."""
        body: JsonObject = {"reply": reply}
        if message is not None:
            body["message"] = message
        await self._send("POST", f"/permission/{request_id}/reply", directory, body)

    async def add_mcp(self, directory: Path, name: str, config: JsonObject) -> None:
        """Give the agents of ``directory`` the tools of the MCP server ``config`` describes, under ``name``, once
        OpenCode has connected to it. OpenCode keeps it in memory only, so a server started later has none of these."""
        response: httpx.Response = await self._send("POST", "/mcp", directory, {"name": name, "config": config})
        status: str = get_str(get_obj(parse_json_object(response.text, "opencode /mcp"), name), "status")
        if status != "connected":
            raise OpenCodeError(
                "OpenCode did not connect to Dot's tools",
                {"name": name, "status": status, "body": response.text[:2000]},
            )

    async def drop_mcp(self, directory: Path, name: str) -> None:
        """Let the MCP server ``name`` go: OpenCode disconnects from it and its tools leave every session."""
        await self._send("POST", f"/mcp/{name}/disconnect", directory, None)

    async def keep_to(self, session_id: str, directory: Path, rules: list[JsonValue]) -> None:
        """Set the permission rules of the session, over its agent's (the last rule that matches a tool decides)."""
        await self._send("PATCH", f"/session/{session_id}", directory, {"permission": rules})

    @asynccontextmanager
    async def events(self, directory: Path) -> AsyncIterator[AsyncIterator[JsonObject]]:
        """OpenCode's event stream for ``directory``, open from entering on (open it before the prompt, so that no
        event of the prompt is missed); each item is one event as OpenCode sends it. The stream ends when the server
        stops; a stream that breaks raises :class:`OpenCodeError` where it is read."""
        client: httpx.AsyncClient = await self._client()
        try:
            async with client.stream(
                "GET",
                "/event",
                params={"directory": str(directory)},
                timeout=httpx.Timeout(None, connect=_CONNECT_TIMEOUT_S),
            ) as response:
                if response.status_code != 200:
                    raise OpenCodeError("OpenCode's event stream did not open", {"status": response.status_code})
                yield _events(response)
        except httpx.TransportError as error:
            raise OpenCodeError(
                "OpenCode's event stream could not be opened", {"error": f"{type(error).__name__}: {error}"}
            ) from error

    async def aclose(self) -> None:
        if self._idle is not None:
            self._idle.cancel()
            with suppress(asyncio.CancelledError):
                await self._idle
        async with self._lock:
            if self._live is not None:
                await self._release(self._live)
                self._live = None


async def _events(response: httpx.Response) -> AsyncIterator[JsonObject]:
    """The events of OpenCode's stream. Whatever ends it early is an ``OpenCodeError``, a line that is not a JSON object
    too (item 351: that one ended the reader of the stream without a word, and the run waited for it for good)."""
    try:
        async for line in response.aiter_lines():
            if line.startswith("data:"):
                data: str = line.removeprefix("data:").strip()
                try:
                    yield parse_json_object(data, "opencode event")
                except JsonParseError as error:
                    raise OpenCodeError(
                        "OpenCode's event stream sent a line that is not a JSON object",
                        {"line": data[:_EVENT_LINE_CHARS], "error": error.message},
                    ) from error
    except httpx.HTTPError as error:
        raise OpenCodeError("OpenCode's event stream broke", {"error": f"{type(error).__name__}: {error}"}) from error


def _read_models(path: Path) -> _ModelList | None:
    if not path.exists():
        return None
    try:
        return _ModelList.model_validate_json(path.read_bytes())
    except ValueError:
        log.warning("opencode_models_unreadable", path=str(path))
        return None


def _write_models(path: Path, models: _ModelList) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(models.model_dump_json(indent=2) + "\n", encoding="utf-8")
