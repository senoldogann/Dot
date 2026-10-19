"""The tokens of an account signed in (item 422), in the login Keychain under the service ``dot-oauth``, one item an
account (``<provider>:<address>``), as the hex of their JSON. They are written through ``security``'s own prompt
(``security -i``, read from its standard input), so that no token is ever on a command line another process could
read. ``DOT_KEYCHAIN`` names another keychain file, so that tests never touch the owner's."""

import asyncio
import json
import os
import re

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
# An address that ``security``'s prompt takes as one word: no space, no quote, no backslash.
_PLAIN: re.Pattern[str] = re.compile(r"[^\s\"'\\]+")


def keychain_file() -> str | None:
    """The keychain file ``DOT_KEYCHAIN`` names for the tests, None for the owner's login keychain."""
    named: str | None = os.environ.get("DOT_KEYCHAIN")
    return named if named is not None and named != "" else None


def item_account(provider: ProviderId, address: str) -> str:
    """The account of the Keychain item of ``address`` at ``provider``."""
    if _PLAIN.fullmatch(address) is None:
        raise SignInError("the account's address cannot name a Keychain item", {"provider": provider})
    return f"{provider}:{address}"


class Keychain:
    """The tokens of the accounts signed in, in ``file`` (None: the login keychain)."""

    def __init__(self, file: str | None) -> None:
        self._file: tuple[str, ...] = () if file is None else (file,)

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

    async def save(self, provider: ProviderId, address: str, tokens: Tokens) -> None:
        account: str = item_account(provider, address)
        value: str = tokens.model_dump_json().encode("utf-8").hex()
        line: str = " ".join(("add-generic-password", "-U", "-s", SERVICE, "-a", account, "-w", value, *self._file))
        returncode, _, err = await self._run(("-i",), f"{line}\n".encode())
        if returncode != 0 or err.strip() != "":
            raise SignInError(
                "the Keychain did not keep the account's tokens",
                {"provider": provider, "returncode": returncode, "error": truncate_middle(err.strip(), 300)},
            )

    async def read(self, provider: ProviderId, address: str) -> Tokens | None:
        """The account's tokens, None when the Keychain holds none."""
        account: str = item_account(provider, address)
        returncode, out, err = await self._run(
            ("find-generic-password", "-s", SERVICE, "-a", account, "-w", *self._file), None
        )
        if returncode == _NOT_FOUND:
            return None
        if returncode != 0:
            raise SignInError(
                "the Keychain did not give the account's tokens",
                {"provider": provider, "returncode": returncode, "error": truncate_middle(err.strip(), 300)},
            )
        try:
            return Tokens.model_validate(json.loads(bytes.fromhex(out.strip()).decode("utf-8")))
        except (ValueError, ValidationError) as error:
            raise SignInError(
                "the account's tokens in the Keychain could not be read", {"provider": provider}
            ) from error

    async def forget(self, provider: ProviderId, address: str) -> None:
        """Remove the account's tokens; an account the Keychain holds none of is gone already."""
        account: str = item_account(provider, address)
        returncode, _, err = await self._run(
            ("delete-generic-password", "-s", SERVICE, "-a", account, *self._file), None
        )
        if returncode not in (0, _NOT_FOUND):
            raise SignInError(
                "the Keychain did not remove the account's tokens",
                {"provider": provider, "returncode": returncode, "error": truncate_middle(err.strip(), 300)},
            )
