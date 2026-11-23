"""The models of the owner's Claude plan as the ``claude`` program itself lists them (item 1367), in place of a list
written into Dot: a new model (Fable 5.2) shows up as Claude Code offers it, with its name, its reasoning levels and
whether it has fast mode.

The program says them in its answer to an SDK host's ``initialize`` request on a ``stream-json`` input (measured on
2.1.330 on 2026-11-23: under a second to 11 s, no prompt sent, no model called). It reads the plan's full list over its
telemetry channel: with ``CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`` or ``DISABLE_TELEMETRY`` set it offered five
models, without them fourteen. So this one start of the program, which sends no prompt, goes with its telemetry on
(:func:`listing_env`); its error reports, its updater and its ``/bug`` stay off, and a run's start keeps them all off.
The list is kept in Dot's home for :data:`KEEP_S`, so that the models page, a run's check of its
model and the sync questions (fast mode, a model's name) read it without starting the program again."""

import asyncio
import contextlib
import json
import os
import signal
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path

from pydantic import ConfigDict, ValidationError

from dotagent.claude_code.env import claude_env
from dotagent.core.errors import ClaudeCodeError
from dotagent.core.logs import get_logger
from dotagent.core.paths import claude_code_dir
from dotagent.core.types import FrozenModel
from dotagent.llm.types import Effort

log = get_logger(__name__)

# How long a listed set of models is kept before the program is asked again.
KEEP_S: float = 6 * 3600
# How long the program may take to answer the request.
ANSWER_S: float = 60.0
_REQUEST_ID: str = "dot-models"
_REQUEST: str = json.dumps({"type": "control_request", "request_id": _REQUEST_ID, "request": {"subtype": "initialize"}})
# The variable that keeps the program from fetching the plan's full list (see the module's docstring), and those that
# turn off, one by one, what it turns off but the telemetry the list comes over.
_LIST_BLOCKER: str = "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"
_STILL_OFF: dict[str, str] = {"DISABLE_ERROR_REPORTING": "1", "DISABLE_BUG_COMMAND": "1", "DISABLE_AUTOUPDATER": "1"}
# Claude Code's reasoning levels as Dot names them.
_EFFORTS: dict[str, Effort] = {"low": "low", "medium": "medium", "high": "high", "xhigh": "xhigh", "max": "max"}
# The names Claude Code takes for the newest model of a family; the list names some of them by the model's id instead
# (Fable 5.2 as ``claude-fable-5-2``), and a chat that chose the alias keeps working.
FAMILY_ALIASES: tuple[str, ...] = ("fable", "opus", "sonnet", "haiku")


class PlanModel(FrozenModel):
    """One model of the plan: ``id`` is what ``--model`` takes, ``resolved`` the model it names, ``label`` its name as
    people know it, ``efforts`` its reasoning levels, lowest first, and ``fast`` whether it has fast mode."""

    id: str
    resolved: str
    label: str
    efforts: tuple[Effort, ...]
    fast: bool


class PlanModels(FrozenModel):
    fetched_at: float
    models: tuple[PlanModel, ...]


class _Listed(FrozenModel):
    """One entry of the program's list, as much of it as Dot reads: the program says more of each model (its
    description, auto mode, thinking off), which is left out."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    value: str
    resolvedModel: str
    displayName: str
    supportedEffortLevels: tuple[str, ...] = ()
    supportsFastMode: bool = False


def catalog_argv(program: str) -> tuple[str, ...]:
    """The command that asks the program for its models: a session that takes requests on its input, with no tool,
    settings file, MCP server, slash command or kept session."""
    return (
        program,
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--tools",
        "",
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--permission-mode",
        "dontAsk",
        "--no-session-persistence",
    )


def listing_env(program_env: Mapping[str, str]) -> dict[str, str]:
    """The environment of the request for the models, from the one a run's program gets: its telemetry on (the list
    comes over it), its error reports, updater and ``/bug`` off."""
    return {name: value for name, value in program_env.items() if name != _LIST_BLOCKER} | _STILL_OFF


def models_of_line(line: str) -> tuple[PlanModel, ...] | None:
    """The models in the program's answer to the request, if ``line`` is that answer, else ``None``. The program's
    ``default`` entry is left out: it is another name of one of the others."""
    try:
        item: object = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(item, dict) or item.get("type") != "control_response":
        return None
    response: object = item.get("response")
    if not isinstance(response, dict) or response.get("request_id") != _REQUEST_ID:
        return None
    body: object = response.get("response")
    listed: object = body.get("models") if isinstance(body, dict) else None
    if not isinstance(listed, list):
        raise ClaudeCodeError("Claude Code's answer lists no models", {"response": line[:2000]})
    try:
        entries: list[_Listed] = [_Listed.model_validate(entry) for entry in listed]
    except ValidationError as error:
        raise ClaudeCodeError(
            "Claude Code listed its models in a shape Dot does not read", {"error": str(error), "response": line[:2000]}
        ) from error
    return tuple(
        PlanModel(
            id=entry.value,
            resolved=entry.resolvedModel,
            label=entry.displayName,
            efforts=tuple(_EFFORTS[level] for level in entry.supportedEffortLevels if level in _EFFORTS),
            fast=entry.supportsFastMode,
        )
        for entry in entries
        if entry.value != "default"
    )


def family_of(model: PlanModel) -> str:
    """The family a model belongs to, by the model it names: ``claude-fable-5-2`` "fable"."""
    return model.resolved.removeprefix("claude-").split("-")[0]


def find_model(models: tuple[PlanModel, ...], name: str) -> PlanModel | None:
    """The model ``name`` names: one of ``models`` by its id, or a family's alias the list does not hold as an id by the
    first (the newest) of that family it lists, under the alias. ``None`` when neither."""
    for model in models:
        if model.id == name:
            return model
    if name not in FAMILY_ALIASES:
        return None
    for model in models:
        if family_of(model) == name:
            return model.model_copy(update={"id": name})
    return None


def _models_file() -> Path:
    return claude_code_dir() / "models.json"


def kept_models() -> PlanModels | None:
    """The models last listed, as kept in Dot's home, whatever their age; ``None`` before the first listing."""
    path: Path = _models_file()
    if not path.exists():
        return None
    try:
        return PlanModels.model_validate_json(path.read_bytes())
    except ValueError:
        log.warning("claude_code_models_unreadable", path=str(path))
        return None


def keep_models(models: PlanModels) -> None:
    path: Path = _models_file()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.write_text(models.model_dump_json(indent=2) + "\n", encoding="utf-8")


def fast_model(name: str) -> bool:
    """Whether the model ``name`` has fast mode, by the models last listed; none has before the first listing."""
    kept: PlanModels | None = kept_models()
    found: PlanModel | None = None if kept is None else find_model(kept.models, name)
    return found is not None and found.fast


def model_labels() -> dict[str, str]:
    """The names of the models last listed, by id, and of the family aliases they answer to (none before the first
    listing)."""
    kept: PlanModels | None = kept_models()
    if kept is None:
        return {}
    named: dict[str, str] = {model.id: model.label for model in kept.models}
    aliases: dict[str, str] = {
        alias: found.label
        for alias in FAMILY_ALIASES
        if alias not in named and (found := find_model(kept.models, alias)) is not None
    }
    return named | aliases


async def _read_answer(process: asyncio.subprocess.Process) -> tuple[PlanModel, ...]:
    if process.stdin is None or process.stdout is None:
        raise ClaudeCodeError("Claude Code was started without its pipes", {"pid": process.pid})
    process.stdin.write(_REQUEST.encode("utf-8") + b"\n")
    await process.stdin.drain()
    while True:
        line: bytes = await process.stdout.readline()
        if not line:
            raise ClaudeCodeError("Claude Code ended before it listed its models", {"exit_code": await process.wait()})
        models: tuple[PlanModel, ...] | None = models_of_line(line.decode("utf-8", "replace"))
        if models is not None:
            return models


async def list_plan_models(program: Path, login_env: Mapping[str, str]) -> tuple[PlanModel, ...]:
    """The plan's models as ``program`` lists them now (see the module's docstring). Raises :class:`ClaudeCodeError`
    when it does not answer within :data:`ANSWER_S` or answers in a shape Dot does not read."""
    parent: Path = claude_code_dir()
    await asyncio.to_thread(parent.mkdir, mode=0o700, parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="models-", dir=parent) as scratch:
        env: dict[str, str] = listing_env(claude_env(login_env, Path(scratch)))
        try:
            process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
                *catalog_argv(str(program)),
                cwd=scratch,
                env=env,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as error:
            raise ClaudeCodeError(
                "Claude Code could not be started", {"program": str(program), "error": str(error)}
            ) from error
        try:
            return await asyncio.wait_for(_read_answer(process), ANSWER_S)
        except TimeoutError as error:
            raise ClaudeCodeError(
                "Claude Code did not list its models in time", {"program": str(program), "timeout_s": ANSWER_S}
            ) from error
        finally:
            if process.returncode is None:
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(process.pid, signal.SIGKILL)
            await process.wait()


async def plan_models(program: Path, login_env: Mapping[str, str]) -> tuple[PlanModel, ...]:
    """The plan's models: those kept while younger than :data:`KEEP_S`, else listed again and kept."""
    kept: PlanModels | None = await asyncio.to_thread(kept_models)
    if kept is not None and time.time() - kept.fetched_at < KEEP_S:
        return kept.models
    models: tuple[PlanModel, ...] = await list_plan_models(program, login_env)
    await asyncio.to_thread(keep_models, PlanModels(fetched_at=time.time(), models=models))
    log.info("claude_code_models_listed", models=[model.id for model in models])
    return models
