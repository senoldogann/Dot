"""Changing one setting of the user's ``config.toml`` in place: a ``[browser]`` one (the mode, or whether Chrome is
raised), the default model, ``[models] main`` (item 199), a model of Dot's own work, ``[models] fast``, ``review`` or
``vision`` (item 232), whether the daemon resumes on its own the runs it cut off, ``[agent] resume`` (item 231), or the
context size of a model, a key of ``[models.context]`` (item 250), whether the typed judge answers, ``[judge] provider``
(item 255), whether a run's processes start in the macOS sandbox, ``[shell] sandbox`` (item 262), or the names the
daemon answers to and whether it keeps the Mac awake, ``[server] reach`` and ``keep_awake`` (item 271), so the comments
and the other settings stay as written."""

import os
import re
import tempfile
import threading
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from dotagent.config import JudgeProvider, ResumeMode
from dotagent.core.errors import ConfigError
from dotagent.core.types import JsonObject
from dotagent.llm.roles import ModelRole
from dotagent.store.models import BrowserMode

_TABLE_HEADER: re.Pattern[str] = re.compile(r"^\s*\[")
# A key TOML takes without quotes; any other (a model reference has a colon) is written in double quotes.
_BARE_KEY: re.Pattern[str] = re.compile(r"[A-Za-z0-9_-]+")
# The table of the context sizes of models (item 250).
_CONTEXT_TABLE: str = "models.context"
_FILE_MODE: int = 0o600
# One edit at a time in this process: each reads the file, edits it and replaces it, so two at once (the browser mode
# and raise_window changed together from the web UI) would keep only the later one.
_EDITING: threading.Lock = threading.Lock()

# The tables this module changes a setting of, the settings of each, and the TOML literal of each value.
type ConfigTable = Literal["agent", "assistant", "browser", "judge", "models", "server", "shell", "snapshots"]
type AgentSetting = Literal["resume"]
type AssistantSetting = Literal["name", "owner", "introduced"]
type BrowserSetting = Literal[
    "mode",
    "raise_window",
    "debug_port",
    "enabled",
    "profile",
    "fit_window",
    "viewport_width",
    "viewport_height",
    "appearance",
    "zoom",
]
type JudgeSetting = Literal["provider"]
type ModelsSetting = Literal["main", "fast", "review", "vision"]
type ServerSetting = Literal["reach", "keep_awake"]
type ShellSetting = Literal["sandbox"]
type SnapshotsSetting = Literal["enabled", "app_text", "sound", "animations", "flash", "open_dot"]
type SettingKey = (
    AgentSetting
    | AssistantSetting
    | BrowserSetting
    | JudgeSetting
    | ModelsSetting
    | ServerSetting
    | ShellSetting
    | SnapshotsSetting
)
# A list of names is ``[server] reach`` (item 271).
type SettingValue = str | bool | int | list[str]


def _literal(value: SettingValue) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(_literal(item) for item in value) + "]"
    return str(value) if isinstance(value, int) else f'"{value}"'


def _written_key(key: str) -> str:
    """``key`` as this module writes it: bare where TOML takes it so, else in double quotes."""
    return key if _BARE_KEY.fullmatch(key) else f'"{key}"'


def _key_pattern(key: str) -> str:
    """``key`` as a line of the file may write it: bare, or in double or single quotes."""
    quoted: str = f"\"{re.escape(key)}\"|'{re.escape(key)}'"
    return f"(?:{re.escape(key)}|{quoted})" if _BARE_KEY.fullmatch(key) else f"(?:{quoted})"


def _header(table: str) -> re.Pattern[str]:
    """The header of ``table``, a dotted name for a table within a table (``models.context``)."""
    name: str = r"\s*\.\s*".join(re.escape(part) for part in table.split("."))
    return re.compile(rf"^\s*\[\s*{name}\s*\]\s*(#.*)?$")


def _key_line(key: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*{_key_pattern(key)}\s*=")


def _key_value(key: str) -> re.Pattern[str]:
    return re.compile(
        rf"""^(\s*{_key_pattern(key)}\s*=\s*)("[^"]*"|'[^']*'|true|false|[+-]?[0-9][0-9_]*|\[[^\]]*\])(.*)$"""
    )


def _replaced_line(line: str, key: str, setting: str) -> str:
    """``line`` (a ``key = ...`` line) with its value replaced by ``setting``, a trailing comment kept."""
    match: re.Match[str] | None = _key_value(key).match(line)
    if match is None:
        return f"{_written_key(key)} = {setting}"
    return f"{match.group(1)}{setting}{match.group(3)}"


def _table_lines(lines: list[str], table: str) -> tuple[int, int] | None:
    """The index of the header of ``table`` among ``lines`` and of the line after its last, or ``None`` without one."""
    header: re.Pattern[str] = _header(table)
    start: int | None = next((index for index, line in enumerate(lines) if header.match(line)), None)
    if start is None:
        return None
    end: int = next((index for index in range(start + 1, len(lines)) if _TABLE_HEADER.match(lines[index])), len(lines))
    return start, end


def _key_index(lines: list[str], bounds: tuple[int, int], key: str) -> int | None:
    key_line: re.Pattern[str] = _key_line(key)
    return next((index for index in range(bounds[0] + 1, bounds[1]) if key_line.match(lines[index])), None)


def _with_value(text: str, table: str, key: str, setting: str) -> str:
    """``text`` with ``[table] key`` set to the TOML literal ``setting``; nothing else changes."""
    lines: list[str] = text.splitlines()
    bounds: tuple[int, int] | None = _table_lines(lines, table)
    line: str = f"{_written_key(key)} = {setting}"
    if bounds is None:
        separator: list[str] = [""] if lines and lines[-1].strip() else []
        return "\n".join([*lines, *separator, f"[{table}]", line]) + "\n"
    existing: int | None = _key_index(lines, bounds, key)
    if existing is None:
        return "\n".join([*lines[: bounds[0] + 1], line, *lines[bounds[0] + 1 :]]) + "\n"
    return "\n".join([*lines[:existing], _replaced_line(lines[existing], key, setting), *lines[existing + 1 :]]) + "\n"


def _without_value(text: str, table: str, key: str) -> str:
    """``text`` without the line of ``[table] key``, the table's header kept; ``text`` itself when it has none."""
    lines: list[str] = text.splitlines()
    bounds: tuple[int, int] | None = _table_lines(lines, table)
    existing: int | None = None if bounds is None else _key_index(lines, bounds, key)
    if existing is None:
        return text
    return "\n".join([*lines[:existing], *lines[existing + 1 :]]) + "\n"


def with_setting(text: str, table: ConfigTable, key: SettingKey, value: SettingValue) -> str:
    """``text`` (the contents of a config.toml) with ``[table] key`` set to ``value``; nothing else changes."""
    return _with_value(text, table, key, _literal(value))


def with_context_size(text: str, model: str, tokens: int | None) -> str:
    """``text`` with the context size of ``model`` (``provider:model``) in ``[models.context]`` set to ``tokens``, or
    left out for ``None``; nothing else changes."""
    if tokens is None:
        return _without_value(text, _CONTEXT_TABLE, model)
    return _with_value(text, _CONTEXT_TABLE, model, _literal(tokens))


def with_browser_setting(text: str, key: BrowserSetting, value: SettingValue) -> str:
    """``text`` with ``[browser] key`` set to ``value``; nothing else changes."""
    return with_setting(text, "browser", key, value)


def with_browser_mode(text: str, mode: BrowserMode) -> str:
    """``text`` with ``[browser] mode`` set to ``mode``; nothing else changes."""
    return with_setting(text, "browser", "mode", mode)


def _without(settings: JsonObject, keys: tuple[str, ...]) -> JsonObject:
    """``settings`` with the one value at ``keys`` (its tables, then its key) left out, and each of its tables that this
    leaves empty too (a file without the table and a file with an empty one are the same settings), to compare a file
    before and after."""
    first, *rest = keys
    if not rest:
        return {key: value for key, value in settings.items() if key != first}
    values: object = settings.get(first)
    if not isinstance(values, dict):
        return settings
    others: JsonObject = {key: value for key, value in settings.items() if key != first}
    remaining: JsonObject = _without(values, tuple(rest))
    return {**others, first: remaining} if remaining else others


def _value_at(settings: JsonObject, keys: tuple[str, ...]) -> object:
    """The value at ``keys`` in ``settings``, ``None`` where there is none."""
    value: object = settings
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _parse(text: str, path: Path) -> JsonObject:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError("config file is not valid TOML", {"path": str(path), "error": str(error)}) from error


def _by_hand(by_hand: str, path: Path, reason: str) -> ConfigError:
    return ConfigError(
        f"the config file could not be changed automatically; {by_hand} by hand", {"path": str(path), "reason": reason}
    )


def set_browser_mode(path: Path, mode: BrowserMode) -> None:
    """Write ``[browser] mode`` into the config file at ``path`` (see :func:`set_setting`)."""
    set_setting(path, "browser", "mode", mode)


def set_browser_setting(path: Path, key: BrowserSetting, value: SettingValue) -> None:
    """Write ``[browser] key = value`` into the config file at ``path`` (see :func:`set_setting`)."""
    set_setting(path, "browser", key, value)


def set_default_model(path: Path, model: str) -> None:
    """Write ``[models] main``, the model a run that names none starts on, into the config file at ``path`` (see
    :func:`set_setting`)."""
    set_setting(path, "models", "main", model)


def set_role_model(path: Path, role: ModelRole, model: str) -> None:
    """Write ``[models] fast``, ``review`` or ``vision``, the model of that part of Dot's own work, into the config file
    at ``path`` (see :func:`set_setting`)."""
    set_setting(path, "models", role, model)


def set_resume(path: Path, mode: ResumeMode) -> None:
    """Write ``[agent] resume``, whether the daemon resumes on its own, when it starts, the runs it cut off, into the
    config file at ``path`` (see :func:`set_setting`)."""
    set_setting(path, "agent", "resume", mode)


def set_judge_provider(path: Path, provider: JudgeProvider) -> None:
    """Write ``[judge] provider``, whether the typed judge answers through TypeSafe or answers nothing, into the config
    file at ``path`` (see :func:`set_setting`)."""
    set_setting(path, "judge", "provider", provider)


def set_shell_sandbox(path: Path, on: bool) -> None:
    """Write ``[shell] sandbox``, whether the processes of a run outside yolo start in the macOS sandbox, into the
    config file at ``path`` (see :func:`set_setting`)."""
    set_setting(path, "shell", "sandbox", on)


def set_setting(path: Path, table: ConfigTable, key: SettingKey, value: SettingValue) -> None:
    """Write ``[table] key = value`` into the config file at ``path`` (created when missing). The result is parsed again
    before it replaces the file: it must hold the new value and every other setting unchanged, or nothing is
    written."""
    _edit(
        path,
        (table, key),
        value,
        lambda text: with_setting(text, table, key, value),
        f"add {key} = {_literal(value)} under [{table}]",
    )


def set_context_size(path: Path, model: str, tokens: int | None) -> None:
    """Write the context size of ``model`` into ``[models.context]`` of the config file at ``path`` (created when
    missing), or take it out for ``None``; the result is checked as :func:`set_setting` checks its own."""
    _edit(
        path,
        ("models", "context", model),
        tokens,
        lambda text: with_context_size(text, model, tokens),
        f"remove {_written_key(model)} from [{_CONTEXT_TABLE}]"
        if tokens is None
        else f"add {_written_key(model)} = {tokens} under [{_CONTEXT_TABLE}]",
    )


def _edit(
    path: Path, keys: tuple[str, ...], value: SettingValue | None, change: Callable[[str], str], by_hand: str
) -> None:
    """Replace the config file at ``path`` with ``change`` of its text, once the result, parsed again, holds
    ``value`` at ``keys`` (nothing there for ``None``) and every other setting unchanged; else nothing is written and
    the error says to do ``by_hand`` by hand."""
    with _EDITING:
        try:
            before: str = path.read_text(encoding="utf-8") if path.exists() else ""
        except UnicodeDecodeError as error:
            raise _by_hand(by_hand, path, f"the file is not UTF-8 text: {error}") from error
        old_settings: JsonObject = _parse(before, path)
        after: str = change(before)
        try:
            new_settings: JsonObject = tomllib.loads(after)
        except tomllib.TOMLDecodeError as error:
            raise _by_hand(by_hand, path, f"the edited file is not valid TOML: {error}") from error
        held: object = _value_at(new_settings, keys)
        # A type of its own as well: TOML's true is not an integer 1, nor a string "true".
        if held != value or (value is not None and type(held) is not type(value)):
            raise _by_hand(by_hand, path, "the edited file does not hold the new value")
        if _without(new_settings, keys) != _without(old_settings, keys):
            raise _by_hand(by_hand, path, "the edit changed another setting")
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(after)
            os.chmod(temporary, path.stat().st_mode & 0o777 if path.exists() else _FILE_MODE)
            os.replace(temporary, path)
        except OSError:
            Path(temporary).unlink(missing_ok=True)
            raise
