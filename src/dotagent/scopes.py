"""The scopes the owner may test (item 1888, docs/recon.md): a bug bounty program's hosts, in one file of their own.

``scopes.toml`` in Dot's home is the one place a scope is written, and it is Dot that writes it — ``dotx scope add``,
``dotx scope remove``, or a run that read the program's published scope and registered it. Recon and security_scan
never ask who owns a host: they ask which scope covers it, and this store answers with the program and its rules.

The file is read on every call by its stamp, so a scope added in one run holds for the next one without a restart:

```toml
[[scopes]]
program = "Bugcrowd · Klaviyo"
hosts = ["a.klaviyo.com", "klaviyo.com"]
url = "https://bugcrowd.com/engagements/klaviyo-og"
note = "no scanners; 5 req/s"
```
"""

import tomllib
from pathlib import Path

from pydantic import ValidationError

from dotagent.config import ReconAuthority, ReconConfig, ReconScope
from dotagent.core.errors import ConfigError
from dotagent.core.files import write_whole
from dotagent.core.logs import get_logger
from dotagent.core.paths import dot_home

log = get_logger(__name__)


class ScopesFileError(ConfigError):
    """``scopes.toml`` cannot be read as scopes: it is not TOML, or an entry in it is not a valid scope. The message
    names the file and what is wrong, so the owner can fix the line or run ``dotx scope add`` again."""


_HEADER: str = (
    "# The programs and engagements the owner may test (item 1888, docs/recon.md).\n"
    "# Written by `dotx scope add` and `dotx scope remove`; a host in a scope is treated as an owned host.\n"
    "\n"
)

# The scopes of the file last read, by the file, its stamp and its size: reading it for every tool call is cheap and a
# write from another process lands on the next call.
_read: dict[tuple[str, float, int], tuple[ReconScope, ...]] = {}


def scopes_path() -> Path:
    """The file the scopes are kept in: ``scopes.toml`` in Dot's home."""
    return dot_home() / "scopes.toml"


def _parsed(text: str, file: Path) -> tuple[ReconScope, ...]:
    """The scopes ``text``, the contents of ``file``, holds; :class:`ScopesFileError` naming ``file`` when it is not
    TOML or an entry is not a valid scope."""
    try:
        raw: dict[str, object] = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ScopesFileError(
            f"{file} is not valid TOML ({exc}); fix the line or remove the file", {"file": str(file)}
        ) from exc
    items: object = raw.get("scopes", [])
    if not isinstance(items, list):
        raise ScopesFileError(
            f"{file}: 'scopes' must be a list of [[scopes]] tables, found {type(items).__name__}", {"file": str(file)}
        )
    entries: list[ReconScope] = []
    for index, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise ScopesFileError(f"{file}: scope {index} is not a [[scopes]] table", {"file": str(file)})
        hosts: object = item.get("hosts", [])
        if not isinstance(hosts, list):
            raise ScopesFileError(f"{file}: scope {index} has 'hosts' that is not a list", {"file": str(file)})
        try:
            entries.append(
                ReconScope(
                    program=item.get("program"),
                    hosts=tuple(str(host) for host in hosts),
                    url=item.get("url", ""),
                    note=item.get("note", ""),
                )
            )
        except ValidationError as exc:
            raise ScopesFileError(f"{file}: scope {index} is not a valid scope: {exc}", {"file": str(file)}) from exc
    return tuple(entries)


def load_scopes(path: Path | None = None) -> tuple[ReconScope, ...]:
    """The scopes held in ``path`` (or in :func:`scopes_path`), in the order they were added; empty when the file is
    not there yet."""
    file: Path = path if path is not None else scopes_path()
    try:
        stamp = file.stat()
    except FileNotFoundError:
        return ()
    except OSError as exc:
        raise ScopesFileError(f"{file} cannot be read ({exc})", {"file": str(file)}) from exc
    key: tuple[str, float, int] = (str(file), stamp.st_mtime, stamp.st_size)
    held: tuple[ReconScope, ...] | None = _read.get(key)
    if held is not None:
        return held
    try:
        text: str = file.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ScopesFileError(f"{file} cannot be read as UTF-8 text ({exc})", {"file": str(file)}) from exc
    value: tuple[ReconScope, ...] = _parsed(text, file)
    _read.clear()
    _read[key] = value
    return value


# The escapes a TOML basic string has for the characters it cannot hold as they are.
_ESCAPES: dict[str, str] = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t", "\b": "\\b", "\f": "\\f"}


def _quoted(text: str) -> str:
    """``text`` as a TOML basic string: a backslash, a quote and every control character escaped, so that a program's
    rules of several lines are read back as they were written."""
    escaped: str = "".join(
        _ESCAPES.get(
            character, f"\\u{ord(character):04X}" if ord(character) < 0x20 or ord(character) == 0x7F else character
        )
        for character in text
    )
    return f'"{escaped}"'


def save_scopes(scopes: tuple[ReconScope, ...], path: Path | None = None) -> Path:
    """Write ``scopes`` as the whole file at ``path`` (or at :func:`scopes_path`), and answer the file written."""
    file: Path = path if path is not None else scopes_path()
    lines: list[str] = [_HEADER]
    for scope in scopes:
        lines.append("[[scopes]]\n")
        lines.append(f"program = {_quoted(scope.program)}\n")
        hosts: str = ", ".join(_quoted(host) for host in scope.hosts)
        lines.append(f"hosts = [{hosts}]\n")
        if scope.url:
            lines.append(f"url = {_quoted(scope.url)}\n")
        if scope.note:
            lines.append(f"note = {_quoted(scope.note)}\n")
        lines.append("\n")
    text: str = "".join(lines)
    # The text is read back before it replaces the file, so that a scope reported added is one the gate can read and a
    # text that would not be leaves the scopes held as they were.
    if _parsed(text, file) != scopes:
        raise ScopesFileError(
            f"{file} would not read back as the scopes given; nothing was written", {"file": str(file)}
        )
    file.parent.mkdir(parents=True, exist_ok=True)
    write_whole(file, text)
    return file


def add_scope(
    program: str,
    hosts: tuple[str, ...],
    url: str = "",
    note: str = "",
    path: Path | None = None,
) -> tuple[ReconScope, bool]:
    """Add the scope of ``program``, or replace the one the file holds for it; answer it and whether it replaced one.

    The hosts are written the one way a scope holds them (a bare name, no wildcard, port, scheme or path), and a name
    covers its subdomains. A program added without hosts is still written: the rules and the program page are worth
    keeping, and its hosts come with the next ``add``."""
    entry = ReconScope(program=program.strip(), hosts=hosts, url=url.strip(), note=note.strip())
    held: list[ReconScope] = list(load_scopes(path))
    for index, scope in enumerate(held):
        if scope.program.strip().casefold() == entry.program.casefold():
            held[index] = entry
            save_scopes(tuple(held), path)
            return entry, True
    held.append(entry)
    save_scopes(tuple(held), path)
    return entry, False


def remove_scope(program: str, path: Path | None = None) -> bool:
    """Remove the scope of ``program`` from the file; answer whether one was there."""
    wanted: str = program.strip().casefold()
    held: list[ReconScope] = list(load_scopes(path))
    kept: list[ReconScope] = [scope for scope in held if scope.program.strip().casefold() != wanted]
    if len(kept) == len(held):
        return False
    save_scopes(tuple(kept), path)
    return True


def authorising_if_readable(host: str, recon: ReconConfig) -> ReconAuthority | None:
    """:func:`authorising`, where a scope only adds context to work that does not depend on it (item 1889: the grounds
    of a refusal, the model a security task starts on): a ``scopes.toml`` that cannot be read covers no host there,
    and the warning names the file and what is wrong. The port scan and ``security_scan`` call :func:`authorising`
    and stop on it."""
    try:
        return authorising(host, recon)
    except ScopesFileError as error:
        log.warning("scopes_unreadable", host=host, error=error.message, context=error.context)
        return None


def authorising(host: str, recon: ReconConfig) -> ReconAuthority | None:
    """Who allows a test of ``host``: the owner's own list first, then a scope of the config file, then a scope of the
    store, with the program's name and the rules it wrote. ``None`` when nothing covers it; :class:`ScopesFileError`
    when the store cannot be read."""
    found: ReconAuthority | None = recon.authorising(host)
    if found is not None:
        return found
    wanted: str = host.strip().lower().rstrip(".")
    for scope in load_scopes():
        if any(wanted == entry or wanted.endswith(f".{entry}") for entry in scope.hosts):
            return ReconAuthority(program=scope.program, note=scope.note)
    return None
