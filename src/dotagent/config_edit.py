"""Changing one setting of the user's ``config.toml`` in place, so the comments and the other settings stay as written."""

import os
import re
import tempfile
import tomllib
from pathlib import Path

from dotagent.config import BrowserMode
from dotagent.core.errors import ConfigError
from dotagent.core.types import JsonObject

_TABLE_HEADER: re.Pattern[str] = re.compile(r"^\s*\[")
_BROWSER_HEADER: re.Pattern[str] = re.compile(r"^\s*\[\s*browser\s*\]\s*(#.*)?$")
_MODE_LINE: re.Pattern[str] = re.compile(r"^\s*mode\s*=")
_MODE_VALUE: re.Pattern[str] = re.compile(r"""^(\s*mode\s*=\s*)("[^"]*"|'[^']*')(.*)$""")
_FILE_MODE: int = 0o600


def _replaced_mode_line(line: str, setting: str) -> str:
    """``line`` (a ``mode = ...`` line) with its value replaced by ``setting``, a trailing comment kept."""
    match: re.Match[str] | None = _MODE_VALUE.match(line)
    if match is None:
        return f"mode = {setting}"
    return f"{match.group(1)}{setting}{match.group(3)}"


def with_browser_mode(text: str, mode: BrowserMode) -> str:
    """``text`` (the contents of a config.toml) with ``[browser] mode`` set to ``mode``; nothing else changes."""
    setting: str = f'"{mode}"'
    lines: list[str] = text.splitlines()
    start: int | None = next((index for index, line in enumerate(lines) if _BROWSER_HEADER.match(line)), None)
    if start is None:
        separator: list[str] = [""] if lines and lines[-1].strip() else []
        return "\n".join([*lines, *separator, "[browser]", f"mode = {setting}"]) + "\n"
    end: int = next((index for index in range(start + 1, len(lines)) if _TABLE_HEADER.match(lines[index])), len(lines))
    existing: int | None = next((index for index in range(start + 1, end) if _MODE_LINE.match(lines[index])), None)
    if existing is None:
        return "\n".join([*lines[: start + 1], f"mode = {setting}", *lines[start + 1 :]]) + "\n"
    return "\n".join([*lines[:existing], _replaced_mode_line(lines[existing], setting), *lines[existing + 1 :]]) + "\n"


def _without_browser_mode(settings: JsonObject) -> JsonObject:
    """``settings`` with the one value this module changes left out, and the browser table too when that leaves it
    empty (a file without the table and a file with an empty one are the same settings), to compare a file before and
    after."""
    browser: object = settings.get("browser")
    if not isinstance(browser, dict):
        return settings
    others: JsonObject = {key: value for key, value in settings.items() if key != "browser"}
    remaining: JsonObject = {key: value for key, value in browser.items() if key != "mode"}
    return {**others, "browser": remaining} if remaining else others


def _parse(text: str, path: Path) -> JsonObject:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError("config file is not valid TOML", {"path": str(path), "error": str(error)}) from error


def _by_hand(mode: BrowserMode, path: Path, reason: str) -> ConfigError:
    return ConfigError(
        f'the config file could not be changed automatically; add mode = "{mode}" under [browser] by hand',
        {"path": str(path), "reason": reason},
    )


def set_browser_mode(path: Path, mode: BrowserMode) -> None:
    """Write ``[browser] mode`` into the config file at ``path`` (created when missing). The result is parsed again
    before it replaces the file: it must hold the new mode and every other setting unchanged, or nothing is written."""
    before: str = path.read_text(encoding="utf-8") if path.exists() else ""
    old_settings: JsonObject = _parse(before, path)
    after: str = with_browser_mode(before, mode)
    try:
        new_settings: JsonObject = tomllib.loads(after)
    except tomllib.TOMLDecodeError as error:
        raise _by_hand(mode, path, f"the edited file is not valid TOML: {error}") from error
    browser: object = new_settings.get("browser")
    if not isinstance(browser, dict) or browser.get("mode") != mode:
        raise _by_hand(mode, path, "the edited file does not hold the new mode")
    if _without_browser_mode(new_settings) != _without_browser_mode(old_settings):
        raise _by_hand(mode, path, "the edit changed another setting")
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
