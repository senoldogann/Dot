"""The tokens of an account signed in (item 422), in the login Keychain under the service ``dot-oauth``.

An account's tokens are the base64 of their JSON, cut into pieces: ``security``'s prompt reads a line of at most 4096
bytes, and Microsoft's tokens alone are longer. The pieces are in one of two slots, ``a`` and ``b``: the items
``<provider>:<address>#a0``, ``#a1``… hold them, and the item ``<provider>:<address>`` names the slot and how many
pieces it holds (``a2``). New tokens go to the other slot, and that item names it only once every piece is kept, so
that tokens cut off half-written leave the tokens before them whole. The pieces are written and read through
``security``'s own prompt (``security -i``, read from its standard input), so that no token is ever on a command line
another process could read, and anything that looks like a piece is cut out of what ``security`` says before an error
keeps it. ``DOT_KEYCHAIN`` names another keychain file, so that tests never touch the owner's."""

import asyncio
import base64
import json
import os
import re
from typing import Literal

from pydantic import ValidationError

from dotagent.core.errors import SignInError
from dotagent.core.text import truncate_middle
from dotagent.oauth.providers import ProviderId
from dotagent.oauth.tokens import Tokens

SERVICE: str = "dot-oauth"
_SECURITY: str = "/usr/bin/security"
_TIMEOUT_S: float = 15.0
# ``security``'s exit status for an item that is not there.
_NOT_FOUND: int = 44
# An address that ``security``'s prompt takes as one word (no space, no quote, no backslash), short enough for a line
# of the prompt to hold it with a piece.
_PLAIN: re.Pattern[str] = re.compile(r"[^\s\"'\\]{1,320}")
# A keychain file's path that ``security``'s prompt takes as one word.
_PLAIN_PATH: re.Pattern[str] = re.compile(r"[^\s\"'\\]+")
# The characters of a piece, and the bytes of a line of ``security``'s prompt, its end of line included.
_PIECE_CHARS: int = 3000
_LINE_BYTES: int = 4095
# More pieces than a slot ever holds: the pieces a slot keeps from longer tokens before are looked for up to here.
_MOST_PIECES: int = 32
# What says which slot holds the pieces, and how many.
_SLOT_COUNT: re.Pattern[str] = re.compile(r"([ab])([1-9][0-9]?)")
# A run of characters long enough to be part of a piece, in what ``security`` says.
_TOKEN_LIKE: re.Pattern[str] = re.compile(r"[A-Za-z0-9_\-=]{40,}")

type Slot = Literal["a", "b"]


def keychain_file() -> str | None:
    """The keychain file ``DOT_KEYCHAIN`` names for the tests, None for the owner's login keychain."""
    named: str | None = os.environ.get("DOT_KEYCHAIN")
    return named if named is not None and named != "" else None


def item_account(provider: ProviderId, address: str) -> str:
    """The account of the Keychain item that names the slot of the tokens of ``address`` at ``provider``."""
    if _PLAIN.fullmatch(address) is None:
        raise SignInError("the account's address cannot name a Keychain item", {"provider": provider})
    return f"{provider}:{address}"


def pieces_of(tokens: Tokens) -> list[str]:
    """The pieces ``tokens`` are kept in: the base64 of their JSON, cut every :data:`_PIECE_CHARS` characters."""
    text: str = base64.urlsafe_b64encode(tokens.model_dump_json().encode("utf-8")).decode("ascii")
    return [text[start : start + _PIECE_CHARS] for start in range(0, len(text), _PIECE_CHARS)]


def tokens_of(pieces: list[str]) -> Tokens:
    """The tokens ``pieces`` hold; a ValueError or a ValidationError when they are not whole."""
    return Tokens.model_validate(json.loads(base64.urlsafe_b64decode("".join(pieces)).decode("utf-8")))


def without_pieces(said: str) -> str:
    """What ``security`` said, shortened, with every run that could be part of a piece cut out."""
    return truncate_middle(_TOKEN_LIKE.sub("[cut]", said.strip()), 300)


def _piece_item(account: str, slot: Slot, index: int) -> str:
    return f"{account}#{slot}{index}"


class Keychain:
    """The tokens of the accounts signed in, in ``file`` (None: the login keychain). One account's tokens are read and
    written one at a time: two saves at once would mix their pieces in one slot."""

    def __init__(self, file: str | None) -> None:
        if file is not None and _PLAIN_PATH.fullmatch(file) is None:
            raise SignInError(
                "the keychain file's path has a space, a quote or a backslash, which security's prompt would split",
                {"file": file},
            )
        self._file: tuple[str, ...] = () if file is None else (file,)
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, account: str) -> asyncio.Lock:
        return self._locks.setdefault(account, asyncio.Lock())

    async def _run(self, arguments: tuple[str, ...], given: bytes | None) -> tuple[int, str, str]:
        try:
            process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
                _SECURITY,
                *arguments,
                stdin=asyncio.subprocess.PIPE if given is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            raise SignInError(f"the Keychain could not be asked: {error.strerror or error}", {}) from error
        try:
            out, err = await asyncio.wait_for(process.communicate(given), _TIMEOUT_S)
        except TimeoutError as error:
            process.kill()
            await process.wait()
            raise SignInError(
                f"the Keychain did not answer in {_TIMEOUT_S:g} s: it may be locked, or wait for a yes on the Mac",
                {"command": arguments[0]},
            ) from error
        returncode: int = process.returncode if process.returncode is not None else -1
        return returncode, out.decode("utf-8", errors="replace"), err.decode("utf-8", errors="replace")

    def _line(self, words: tuple[str, ...]) -> str:
        """One line of ``security``'s prompt; an error rather than a line the prompt would cut in two."""
        line: str = " ".join((*words, *self._file))
        size: int = len(line.encode("utf-8")) + 1
        if size > _LINE_BYTES:
            raise SignInError("a line for the Keychain's prompt is too long", {"bytes": size, "most": _LINE_BYTES})
        return line

    async def _prompt(self, lines: list[str], failed: str, provider: ProviderId) -> str:
        """What ``security``'s prompt prints for ``lines``; an error that says ``failed`` when one of them fails."""
        returncode, out, err = await self._run(("-i",), "".join(f"{line}\n" for line in lines).encode("utf-8"))
        if returncode != 0 or err.strip() != "":
            raise SignInError(failed, {"provider": provider, "returncode": returncode, "error": without_pieces(err)})
        return out

    async def _slot(self, provider: ProviderId, account: str) -> tuple[Slot, int] | None:
        """The slot that holds the account's pieces and how many it holds; None when the Keychain holds no tokens of
        the account."""
        returncode, out, err = await self._run(
            ("find-generic-password", "-s", SERVICE, "-a", account, "-w", *self._file), None
        )
        if returncode == _NOT_FOUND:
            return None
        if returncode != 0:
            raise SignInError(
                "the Keychain did not give the account's tokens",
                {"provider": provider, "returncode": returncode, "error": without_pieces(err)},
            )
        named: re.Match[str] | None = _SLOT_COUNT.fullmatch(out.strip())
        if named is None:
            raise SignInError("the account's tokens in the Keychain could not be read", {"provider": provider})
        slot: Slot = "a" if named[1] == "a" else "b"
        return slot, int(named[2])

    async def _remove(self, provider: ProviderId, account: str) -> bool:
        """Remove one item; False when it is not there."""
        returncode, _, err = await self._run(
            ("delete-generic-password", "-s", SERVICE, "-a", account, *self._file), None
        )
        if returncode == _NOT_FOUND:
            return False
        if returncode != 0:
            raise SignInError(
                "the Keychain did not remove the account's tokens",
                {"provider": provider, "returncode": returncode, "error": without_pieces(err)},
            )
        return True

    async def _clear(self, provider: ProviderId, account: str, slot: Slot, start: int) -> None:
        """Remove the pieces of ``slot`` from ``start`` on, up to the first that is not there."""
        for index in range(start, _MOST_PIECES):
            if not await self._remove(provider, _piece_item(account, slot, index)):
                return

    async def save(self, provider: ProviderId, address: str, tokens: Tokens) -> None:
        account: str = item_account(provider, address)
        async with self._lock(account):
            pieces: list[str] = pieces_of(tokens)
            if len(pieces) >= _MOST_PIECES:
                raise SignInError("the account's tokens are too long for the Keychain", {"provider": provider})
            before: tuple[Slot, int] | None = await self._slot(provider, account)
            slot: Slot = "b" if before is not None and before[0] == "a" else "a"
            lines: list[str] = [
                self._line(
                    ("add-generic-password", "-U", "-s", SERVICE, "-a", _piece_item(account, slot, at), "-w", piece)
                )
                for at, piece in enumerate(pieces)
            ]
            await self._prompt(lines, "the Keychain did not keep the account's tokens", provider)
            # Only once every piece is kept does the item name their slot; what it holds is no secret.
            returncode, _, err = await self._run(
                ("add-generic-password", "-U", "-s", SERVICE, "-a", account, "-w", f"{slot}{len(pieces)}", *self._file),
                None,
            )
            if returncode != 0:
                raise SignInError(
                    "the Keychain did not keep the account's tokens",
                    {"provider": provider, "returncode": returncode, "error": without_pieces(err)},
                )
            # The slot named now may hold more pieces from longer tokens before; the other slot is not needed any more.
            await self._clear(provider, account, slot, len(pieces))
            if before is not None:
                await self._clear(provider, account, before[0], 0)

    async def read(self, provider: ProviderId, address: str) -> Tokens | None:
        """The account's tokens, None when the Keychain holds none."""
        account: str = item_account(provider, address)
        async with self._lock(account):
            found: tuple[Slot, int] | None = await self._slot(provider, account)
            if found is None:
                return None
            slot, count = found
            lines: list[str] = [
                self._line(("find-generic-password", "-s", SERVICE, "-a", _piece_item(account, slot, index), "-w"))
                for index in range(count)
            ]
            out: str = await self._prompt(lines, "the Keychain did not give the account's tokens", provider)
            pieces: list[str] = out.split("\n")[:count]
            if len(pieces) != count:
                raise SignInError(
                    "the account's tokens in the Keychain are not whole",
                    {"provider": provider, "pieces": count, "found": len(pieces)},
                )
            try:
                return tokens_of(pieces)
            except (ValueError, ValidationError) as error:
                raise SignInError(
                    "the account's tokens in the Keychain could not be read", {"provider": provider, "pieces": count}
                ) from error

    async def forget(self, provider: ProviderId, address: str) -> None:
        """Remove the account's tokens; an account the Keychain holds none of is gone already. The item that names the
        slot goes first, so that pieces a failure leaves behind are never read as tokens."""
        account: str = item_account(provider, address)
        async with self._lock(account):
            await self._remove(provider, account)
            await self._clear(provider, account, "a", 0)
            await self._clear(provider, account, "b", 0)
