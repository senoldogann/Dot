"""Changing one ``[browser]`` setting of the user's ``config.toml`` in place (the mode, or whether Chrome is raised),
so the comments and the other settings stay as written."""

import os
import re
import tempfile
import tomllib
from pathlib import Path
from typing import Literal

from dotagent.core.errors import ConfigError
from dotagent.core.types import JsonObject
from dotagent.store.models import BrowserMode

_TABLE_HEADER: re.Pattern[str] = re.compile(r"^\s*\[")
_BROWSER_HEADER: re.Pattern[str] = re.compile(r"^\s*\[\s*browser\s*\]\s*(#.*)?$")
_FILE_MODE: int = 0o600

# The [browser] settings this module changes, and the TOML literal of each value.
type BrowserSetting = Literal["mode", "raise_window"]
type SettingValue = BrowserMode | bool


def _literal(value: SettingValue) -> str:
    return ("true" if value else "false") if isinstance(value, bool) else f'"{value}"'


def _key_line(key: BrowserSetting) -> re.Pattern[str]:
    return re.compile(rf"^\s*{key}\s*=")


def _key_value(key: BrowserSetting) -> re.Pattern[str]:
    return re.compile(rf"""^(\s*{key}\s*=\s*)("[^"]*"|'[^']*'|true|false)(.*)$""")


def _replaced_line(line: str, key: BrowserSetting, setting: str) -> str:
    """``line`` (a ``key = ...`` line) with its value replaced by ``setting``, a trailing comment kept."""
    match: re.Match[str] | None = _key_value(key).match(line)
    if match is None:
        return f"{key} = {setting}"
    return f"{match.group(1)}{setting}{match.group(3)}"


def with_browser_setting(text: str, key: BrowserSetting, value: SettingValue) -> str:
    """``text`` (the contents of a config.toml) with ``[browser] key`` set to ``value``; nothing else changes."""
    setting: str = _literal(value)
    lines: list[str] = text.splitlines()
    start: int | None = next((index for index, line in enumerate(lines) if _BROWSER_HEADER.match(line)), None)
    if start is None:
        separator: list[str] = [""] if lines and lines[-1].strip() else []
        return "\n".join([*lines, *separator, "[browser]", f"{key} = {setting}"]) + "\n"
    end: int = next((index for index in range(start + 1, len(lines)) if _TABLE_HEADER.match(lines[index])), len(lines))
    key_line: re.Pattern[str] = _key_line(key)
    existing: int | None = next((index for index in range(start + 1, end) if key_line.match(lines[index])), None)
    if existing is None:
        return "\n".join([*lines[: start + 1], f"{key} = {setting}", *lines[start + 1 :]]) + "\n"
    return "\n".join([*lines[:existing], _replaced_line(lines[existing], key, setting), *lines[existing + 1 :]]) + "\n"


def with_browser_mode(text: str, mode: BrowserMode) -> str:
    """``text`` with ``[browser] mode`` set to ``mode``; nothing else changes."""
    return with_browser_setting(text, "mode", mode)


def _without_setting(settings: JsonObject, key: BrowserSetting) -> JsonObject:
    """``settings`` with the one value this module changes left out, and the browser table too when that leaves it
    empty (a file without the table and a file with an empty one are the same settings), to compare a file before and
    after."""
    browser: object = settings.get("browser")
    if not isinstance(browser, dict):
        return settings
    others: JsonObject = {key_: value for key_, value in settings.items() if key_ != "browser"}
    remaining: JsonObject = {key_: value for key_, value in browser.items() if key_ != key}
    return {**others, "browser": remaining} if remaining else others


def _parse(text: str, path: Path) -> JsonObject:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError("config file is not valid TOML", {"path": str(path), "error": str(error)}) from error


def _by_hand(key: BrowserSetting, value: SettingValue, path: Path, reason: str) -> ConfigError:
    return ConfigError(
        f"the config file could not be changed automatically; add {key} = {_literal(value)} under [browser] by hand",
        {"path": str(path), "reason": reason},
    )


def set_browser_mode(path: Path, mode: BrowserMode) -> None:
    """Write ``[browser] mode`` into the config file at ``path`` (see :func:`set_browser_setting`)."""
    set_browser_setting(path, "mode", mode)


def set_browser_setting(path: Path, key: BrowserSetting, value: SettingValue) -> None:
    """Write ``[browser] key = value`` into the config file at ``path`` (created when missing). The result is parsed
    again before it replaces the file: it must hold the new value and every other setting unchanged, or nothing is
    written."""
    before: str = path.read_text(encoding="utf-8") if path.exists() else ""
    old_settings: JsonObject = _parse(before, path)
    after: str = with_browser_setting(before, key, value)
    try:
        new_settings: JsonObject = tomllib.loads(after)
    except tomllib.TOMLDecodeError as error:
        raise _by_hand(key, value, path, f"the edited file is not valid TOML: {error}") from error
    browser: object = new_settings.get("browser")
    if not isinstance(browser, dict) or browser.get(key) != value:
        raise _by_hand(key, value, path, "the edited file does not hold the new value")
    if _without_setting(new_settings, key) != _without_setting(old_settings, key):
        raise _by_hand(key, value, path, "the edit changed another setting")
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
