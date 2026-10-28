"""The logins of a password manager's CSV export, read for Dot's vault (item 621): Chrome's and Edge's, Safari's,
Firefox's, 1Password's and Bitwarden's, each known by its header row. The text comes in the request and is read in
memory: it is never written to a file or a log, and nothing of it is in an error. A row that cannot be a login is
counted by the reason it was left out, and what an export holds besides logins (cards, notes, identities) is left out
the same way. One-time-code seeds are not kept: the vault holds none."""

import csv
import io
from collections import Counter
from typing import Literal

from pydantic import SecretStr

from dotagent.core.errors import InvalidValueError
from dotagent.core.types import FrozenModel
from dotagent.vault.hosts import USERINFO_PROBLEM, site_label, website_problem
from dotagent.vault.secretstore import fits

type ExportFormat = Literal["chrome", "safari", "firefox", "1password", "bitwarden", "other"]

FORMAT_NAMES: dict[ExportFormat, str] = {
    "chrome": "Chrome or Edge",
    "safari": "Safari",
    "firefox": "Firefox",
    "1password": "1Password",
    "bitwarden": "Bitwarden",
    "other": "a password manager",
}

# The most rows one import reads, and the most characters a login's parts may have (the vault's own limits).
MOST_ROWS: int = 20_000
MOST_PASSWORD_CHARS: int = 512
MOST_USERNAME_CHARS: int = 320
MOST_NOTES_CHARS: int = 600
# The reasons a row is left out, in the words the page shows beside the count.
NO_WEBSITE: str = "no website"
NO_PASSWORD: str = "no password"
NOT_A_WEB_ADDRESS: str = "not a web address"
HAS_USERINFO: str = "an address with a user name or a password in it"
NOT_A_LOGIN: str = "not a login (a card, a note or an identity)"
TOO_LONG: str = "a password or username too long"
NOT_ONE_LINE: str = "a password or username with a line break"
ALREADY_SAVED: str = "already in the vault, or twice in the file"
UNCOMPARED: str = "already in the vault, its password not compared (the Keychain did not answer or refused)"
NO_ROOM: str = "already in the vault with another password, not changed: its notes have no room to keep the old one"
CHANGED_MEANWHILE: str = "already in the vault, removed or changed there while the file was read"
OTHER_PASSWORD: str = "a second, different password for a login already in the file (the first is kept)"
# What one cell of an export may hold: a long note is cut to the vault's own limit below, not the whole file refused
# for it (the csv module's own limit is 128 KiB).
_MOST_CELL_CHARS: int = 8 * 1024 * 1024

# The names a column goes by in the exports, in lower case with spaces as underscores.
_WEBSITE: tuple[str, ...] = ("url", "website", "login_uri", "web_site", "site", "uri", "address")
_USERNAME: tuple[str, ...] = ("username", "login_username", "user_name", "user", "login", "email")
_PASSWORD: tuple[str, ...] = ("password", "login_password", "pass")
_NOTES: tuple[str, ...] = ("notes", "note", "extra")
_KIND: tuple[str, ...] = ("type", "category")
# What no one-line field holds: the line breaks Unicode knows, and the character that ends a C string.
_LINE_BREAKS: str = "".join(chr(code) for code in (0x0A, 0x0D, 0x0B, 0x0C, 0x1C, 0x1D, 0x1E, 0x85, 0x2028, 0x2029, 0))


class ParsedLogin(FrozenModel):
    """A login of an export: its website as written, username, password and notes (``None`` when the export has none or
    they are too long to keep)."""

    website: str
    username: str
    password: SecretStr
    notes: SecretStr | None


class ParsedExport(FrozenModel):
    """What an export holds: its format, the logins that can be kept, how many rows it had, how many were left out and
    why, and how many logins lose their notes for being too long."""

    format: ExportFormat
    logins: tuple[ParsedLogin, ...]
    rows: int
    skipped: dict[str, int]
    notes_left_out: int


def _name(header: str) -> str:
    return "_".join(header.replace("\N{ZERO WIDTH NO-BREAK SPACE}", "").strip().lower().split())


def _column(names: list[str], wanted: tuple[str, ...]) -> int | None:
    """The first column whose name is one of ``wanted`` (they are in the order of preference)."""
    for candidate in wanted:
        if candidate in names:
            return names.index(candidate)
    return None


def _format_of(names: list[str]) -> ExportFormat:
    """The export a header row belongs to."""
    if {"login_uri", "login_username", "login_password"} <= set(names):
        return "bitwarden"
    if {"httprealm", "formactionorigin"} & set(names) or "guid" in names:
        return "firefox"
    # 1Password's newer export names a Url and an OTPAuth as Safari's does, and a Title as both do: what tells it is its
    # Website, or the Favorite, Archived and Tags only 1Password writes (item 786).
    if "title" in names and ("website" in names or {"favorite", "archived", "tags"} & set(names)):
        return "1password"
    if "otpauth" in names and "url" in names:
        return "safari"
    if {"name", "url", "username", "password"} <= set(names):
        return "chrome"
    return "other"


def _cell(row: list[str], column: int | None) -> str:
    return row[column].strip() if column is not None and column < len(row) else ""


def _first_address(cell: str) -> str:
    """The first address of a cell that holds several, one to a line (Bitwarden's)."""
    lines: list[str] = [line.strip() for line in cell.splitlines() if line.strip()]
    return lines[0] if lines else ""


def parse_export(text: str) -> ParsedExport:
    """The logins of the CSV ``text``. A text that is not a list of logins, or has more rows than one import takes, is
    an :class:`InvalidValueError` that says so and nothing of what is in it."""
    before: int = csv.field_size_limit(_MOST_CELL_CHARS)
    try:
        rows: list[list[str]] = list(csv.reader(io.StringIO(text.lstrip("\N{ZERO WIDTH NO-BREAK SPACE}")), strict=True))
    except csv.Error as error:
        raise InvalidValueError(
            "this file is not a CSV that Dot can read: export your logins from the password manager as CSV", {}
        ) from error
    finally:
        csv.field_size_limit(before)
    if len(rows) < 2:
        raise InvalidValueError(
            "this file has no logins in it: a CSV export has a header row and a row for each login", {}
        )
    names: list[str] = [_name(header) for header in rows[0]]
    website: int | None = _column(names, _WEBSITE)
    password: int | None = _column(names, _PASSWORD)
    if website is None or password is None:
        raise InvalidValueError(
            "this file is not a list of logins that Dot knows: its first row should name a website (url) and a "
            "password column, as the exports of Chrome, Safari, Firefox, 1Password and Bitwarden do",
            {},
        )
    if len(rows) - 1 > MOST_ROWS:
        raise InvalidValueError(f"this file has more than {MOST_ROWS} rows: import it in parts", {})
    username: int | None = _column(names, _USERNAME)
    notes: int | None = _column(names, _NOTES)
    kind: int | None = _column(names, _KIND)
    skipped: Counter[str] = Counter()
    # The password each login of the file was kept with: a second row of it with another password is said, not lost in
    # silence among the logins already saved (item 786).
    seen: dict[tuple[str, str], str] = {}
    logins: list[ParsedLogin] = []
    notes_left_out: int = 0
    counted: int = 0
    for row in rows[1:]:
        if not any(cell.strip() for cell in row):
            continue
        counted += 1
        kind_text: str = _cell(row, kind).lower()
        if kind_text not in ("", "login", "logins", "password", "passwords"):
            skipped[NOT_A_LOGIN] += 1
            continue
        address: str = _first_address(_cell(row, website))
        secret: str = row[password] if password < len(row) else ""
        if address == "":
            skipped[NO_WEBSITE] += 1
            continue
        if secret == "":
            skipped[NO_PASSWORD] += 1
            continue
        problem: str | None = website_problem(address)
        if problem is not None:
            skipped[HAS_USERINFO if problem == USERINFO_PROBLEM else NOT_A_WEB_ADDRESS] += 1
            continue
        name: str = _cell(row, username)
        # The Keychain keeps an item's secrets as one word whose limit is in bytes (``fits``): a password too much for
        # it alone is no login the vault keeps, and notes that make it too much are left out below.
        if (
            len(secret) > MOST_PASSWORD_CHARS
            or len(name) > MOST_USERNAME_CHARS
            or not fits({"password": SecretStr(secret)})
        ):
            skipped[TOO_LONG] += 1
            continue
        # A username is listed to a run as part of its login's title and both are typed into one-line fields: a break
        # in either is no login the vault keeps (a form refuses it as well).
        if any(char in part for part in (name, secret) for char in _LINE_BREAKS):
            skipped[NOT_ONE_LINE] += 1
            continue
        key: tuple[str, str] = (site_label(address), name.lower())
        if key in seen:
            skipped[ALREADY_SAVED if seen[key] == secret else OTHER_PASSWORD] += 1
            continue
        seen[key] = secret
        note: str = _cell(row, notes)
        if note and (
            len(note) > MOST_NOTES_CHARS or not fits({"password": SecretStr(secret), "notes": SecretStr(note)})
        ):
            notes_left_out += 1
            note = ""
        logins.append(
            ParsedLogin(
                website=address,
                username=name,
                password=SecretStr(secret),
                notes=SecretStr(note) if note else None,
            )
        )
    return ParsedExport(
        format=_format_of(names),
        logins=tuple(logins),
        rows=counted,
        skipped=dict(skipped),
        notes_left_out=notes_left_out,
    )
