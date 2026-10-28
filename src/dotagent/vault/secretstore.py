"""Where Dot's vault keeps its secrets (item 621): the owner's macOS login Keychain, in generic password items of the
service ``dot-vault``, one item for each vault item (the account is the item's id). What an item keeps is a JSON
document of its secret parts (a login's password and notes, a card's number, expiry, security code and name), in base64
so that it is one word of one line.

The Keychain is behind an interface, :class:`SecretStore`, so that a test stands in for it with a dictionary and never
reaches the owner's. The real one, :class:`LoginKeychain`, talks to ``security`` as the accounts' tokens do
(``oauth/keychain.py``): a value is written through ``security -i``, whose commands are read from its standard input, so
that none is ever on a command line another process could read, and read with ``find-generic-password -w``, whose
arguments name the service and the account alone. ``DOT_KEYCHAIN`` names another keychain file, so that tests never
touch the owner's: one that is not there is refused, as ``security`` would use the login keychain in its place. Neither
a value nor the base64 of one is in an error or a log."""

import asyncio
import base64
import binascii
import json
import os
import re
from collections.abc import Mapping, Sequence
from typing import Protocol

from pydantic import JsonValue, SecretStr

from dotagent.core.errors import InvalidValueError, ToolExecutionError
from dotagent.core.text import truncate_middle

SERVICE: str = "dot-vault"
_SECURITY: str = "/usr/bin/security"
_TIMEOUT_S: float = 15.0
# An import writes many items, and a removal of several removes many, in one run of ``security``; it is given longer.
_BATCH_TIMEOUT_S: float = 90.0
# ``security``'s exit status for an item that is not there.
_NOT_FOUND: int = 44
# An account that ``security``'s prompt takes as one word: the letters, digits, hyphen and underscore of an item's id.
_ACCOUNT: re.Pattern[str] = re.compile(r"[A-Za-z0-9_\-]{1,100}")
# A keychain file's path that ``security``'s prompt takes as one word.
_PLAIN_PATH: re.Pattern[str] = re.compile(r"[^\s\"'\\]+")
# What an item's secrets may take, in base64: ``security``'s prompt reads a line of at most 4096 bytes, and the command
# around the word takes some of them.
MOST_CHARS: int = 3000
_LINE_BYTES: int = 4095
# A run of characters long enough to be a value in base64, in what ``security`` says.
_VALUE_LIKE: re.Pattern[str] = re.compile(r"[A-Za-z0-9_\-=]{24,}")
_SAID_CHARS: int = 300
# What an owner whose Keychain refused can do, said after what failed: a locked keychain answers nothing to a program
# with nobody to ask, and a prompt that is not answered times out.
_HINT: str = "check that your Mac's login keychain is unlocked, and answer its prompt if it asks"

type Parts = Mapping[str, SecretStr]


class VaultStoreError(ToolExecutionError):
    """The Keychain did not keep, give or remove an item of the vault. Neither its message nor its context holds a
    value."""


class PartlyRemoved(VaultStoreError):
    """The Keychain refused a removal of several items part way (item 768): ``removed`` are the accounts it had
    removed, or never held, before it refused."""

    def __init__(self, message: str, context: Mapping[str, JsonValue], removed: Sequence[str]) -> None:
        super().__init__(message, context)
        self.removed: tuple[str, ...] = tuple(removed)


class SecretStore(Protocol):
    """What the vault asks of a place that keeps secrets: the parts of an item, by its account (the item's id)."""

    async def put(self, account: str, parts: Parts) -> None:
        """Keep ``parts`` as the secrets of ``account``, replacing what it had."""
        ...

    async def put_many(self, entries: Mapping[str, Parts]) -> None:
        """Keep the secrets of each account of ``entries`` (an import), as :meth:`put` does."""
        ...

    async def get(self, account: str) -> dict[str, SecretStr] | None:
        """The parts kept for ``account``; ``None`` when it has none."""
        ...

    async def remove(self, account: str) -> bool:
        """Remove what is kept for ``account``; ``False`` when nothing was."""
        ...

    async def remove_many(self, accounts: Sequence[str]) -> None:
        """Remove what is kept for each of ``accounts`` (the ticked rows of Settings › Vault), as :meth:`remove`
        does: one that has nothing counts as removed. A refusal part way raises :class:`PartlyRemoved`, which names
        the ones removed before it."""
        ...


def _word(parts: Parts) -> str:
    """The base64 of the JSON of ``parts``, as long as it comes out."""
    document: str = json.dumps(
        {name: value.get_secret_value() for name, value in parts.items()}, ensure_ascii=False, separators=(",", ":")
    )
    return base64.urlsafe_b64encode(document.encode("utf-8")).decode("ascii")


def fits(parts: Parts) -> bool:
    """Whether a word of the prompt holds ``parts`` (:func:`encode_parts` takes them). The limit is in bytes of the
    text, not in its characters: a note of 600 characters of emoji does not fit where one of ASCII does."""
    return len(_word(parts)) <= MOST_CHARS


def encode_parts(parts: Parts) -> str:
    """The parts as one word: the base64 of their JSON. Refused with a message that says what is too long when a word of
    the prompt would not hold it."""
    word: str = _word(parts)
    if len(word) > MOST_CHARS:
        names: list[JsonValue] = [*sorted(parts)]
        raise InvalidValueError(
            "there is too much text to keep in the Keychain for one item: shorten the notes",
            {"parts": names, "limit_chars": MOST_CHARS, "chars": len(word)},
        )
    return word


def decode_parts(word: str) -> dict[str, SecretStr]:
    """The parts that :func:`encode_parts` made of a word; a :class:`VaultStoreError` for anything else, saying nothing
    of what was read."""
    try:
        document: object = json.loads(base64.urlsafe_b64decode(word.strip().encode("ascii")).decode("utf-8"))
    except (ValueError, binascii.Error) as error:
        raise VaultStoreError("an item of the vault in the Keychain could not be read", {}) from error
    if not isinstance(document, dict) or not all(
        isinstance(name, str) and isinstance(value, str) for name, value in document.items()
    ):
        raise VaultStoreError("an item of the vault in the Keychain could not be read", {})
    return {name: SecretStr(value) for name, value in document.items()}


def said(text: str) -> str:
    """What ``security`` said, shortened, with every run that could be a value in base64 cut out."""
    return truncate_middle(_VALUE_LIKE.sub("[cut]", text.strip()), _SAID_CHARS)


class LoginKeychain:
    """The vault's secrets in ``file`` (``None``: the owner's login Keychain). One account is read and written one at a
    time."""

    def __init__(self, file: str | None) -> None:
        if file is not None and _PLAIN_PATH.fullmatch(file) is None:
            raise VaultStoreError(
                "the keychain file's path has a space, a quote or a backslash, which security's prompt would split",
                {"file": file},
            )
        self._file: tuple[str, ...] = () if file is None else (file,)
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, account: str) -> asyncio.Lock:
        return self._locks.setdefault(account, asyncio.Lock())

    @staticmethod
    def _checked(account: str) -> str:
        if _ACCOUNT.fullmatch(account) is None:
            raise VaultStoreError("an item of the vault has an id the Keychain cannot name", {"id": account[:40]})
        return account

    async def _run(self, arguments: tuple[str, ...], given: bytes | None, timeout_s: float) -> tuple[int, str, str]:
        if self._file and not await asyncio.to_thread(os.path.exists, self._file[0]):
            # ``security`` given a keychain file that is not there does not fail: it keeps, reads and removes the item
            # in the default keychain, the owner's login keychain (found out when a test's item was kept there). A file
            # that is not there is refused before anything is asked of it, so that ``DOT_KEYCHAIN`` pointing at a
            # missing file is a refusal and not a way to the owner's own.
            raise VaultStoreError(
                "the keychain file is not there, and security would use the login keychain in its place: Dot asks "
                "only the keychain it was given",
                {"file": self._file[0]},
            )
        try:
            process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
                _SECURITY,
                *arguments,
                stdin=asyncio.subprocess.PIPE if given is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            raise VaultStoreError(f"the Keychain could not be asked: {error.strerror or error}", {}) from error
        try:
            out, err = await asyncio.wait_for(process.communicate(given), timeout_s)
        except TimeoutError as error:
            process.kill()
            await process.wait()
            raise VaultStoreError(
                f"the Keychain did not answer in {timeout_s:g} s: it may be locked, or wait for a yes on the Mac",
                {"command": arguments[0]},
            ) from error
        returncode: int = process.returncode if process.returncode is not None else -1
        return returncode, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")

    def _line(self, account: str, parts: Parts) -> str:
        """The line of the prompt that keeps ``parts`` for ``account``."""
        line: str = " ".join(
            (
                "add-generic-password",
                "-U",
                "-s",
                SERVICE,
                "-a",
                self._checked(account),
                "-w",
                encode_parts(parts),
                *self._file,
            )
        )
        size: int = len(line.encode("utf-8")) + 1
        if size > _LINE_BYTES:
            raise InvalidValueError(
                "there is too much text to keep in the Keychain for one item: shorten the notes",
                {"limit_bytes": _LINE_BYTES, "bytes": size},
            )
        return line

    async def _prompt(self, lines: list[str], failed: str, timeout_s: float) -> None:
        """Run ``lines`` in ``security``'s prompt; an error that says ``failed`` when one of them fails."""
        returncode, _, err = await self._run(("-i",), "".join(f"{line}\n" for line in lines).encode("utf-8"), timeout_s)
        if returncode != 0 or err.strip() != "":
            raise VaultStoreError(f"{failed}: {_HINT}", {"returncode": returncode, "said": said(err)})

    async def put(self, account: str, parts: Parts) -> None:
        line: str = self._line(account, parts)
        async with self._lock(account):
            await self._prompt([line], "the Keychain did not keep the item", _TIMEOUT_S)

    async def put_many(self, entries: Mapping[str, Parts]) -> None:
        lines: list[str] = [self._line(account, parts) for account, parts in entries.items()]
        if lines:
            await self._prompt(lines, "the Keychain did not keep the items", _BATCH_TIMEOUT_S)

    async def get(self, account: str) -> dict[str, SecretStr] | None:
        async with self._lock(account):
            returncode, out, err = await self._run(
                ("find-generic-password", "-s", SERVICE, "-a", self._checked(account), "-w", *self._file),
                None,
                _TIMEOUT_S,
            )
        if returncode == _NOT_FOUND:
            return None
        if returncode != 0:
            raise VaultStoreError(
                f"the Keychain did not give the item: {_HINT}", {"returncode": returncode, "said": said(err)}
            )
        return decode_parts(out)

    async def remove(self, account: str) -> bool:
        async with self._lock(account):
            returncode, _, err = await self._run(
                ("delete-generic-password", "-s", SERVICE, "-a", self._checked(account), *self._file),
                None,
                _TIMEOUT_S,
            )
        if returncode == _NOT_FOUND:
            return False
        if returncode != 0:
            raise VaultStoreError(
                f"the Keychain did not remove the item: {_HINT}", {"returncode": returncode, "said": said(err)}
            )
        return True

    async def remove_many(self, accounts: Sequence[str]) -> None:
        lines: list[str] = [
            " ".join(("delete-generic-password", "-s", SERVICE, "-a", self._checked(account), *self._file))
            for account in accounts
        ]
        if not lines:
            return
        try:
            await self._prompt(lines, "the Keychain did not remove the items", _BATCH_TIMEOUT_S)
        except VaultStoreError:
            # A line that fails fails the prompt, and an account with no item fails its line: each account is asked
            # again alone, in order, where one with no item counts as removed and a Keychain that refuses ends it,
            # naming the ones gone before it, so that their records go too. A refusal of one item alone (a keychain
            # locked part way refuses every later line too) leaves those after it as the prompt left them: one whose
            # item the prompt removed keeps its record, which a removal again takes, its item counting as removed.
            gone: list[str] = []
            for account in accounts:
                try:
                    await self.remove(account)
                except VaultStoreError as refused:
                    raise PartlyRemoved(refused.message, refused.context, gone) from refused
                gone.append(account)
