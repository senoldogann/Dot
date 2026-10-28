"""1Password through its command line, ``op`` (item 493), as 1Password's own guidance has an agent use it: through a
service account that reads only the vaults shared with it, its token given to ``op`` alone, in ``op``'s environment.
Dot reads the logins of those vaults (their titles and websites) for a run to choose from, and a login's username,
password and one-time code at the moment the run fills them into a page, never to show them to its model.

Connecting makes the vault "Shared with Dot" and a service account that only reads it (Grok's flow), with the owner's
own ``op``, signed in through the 1Password app, which asks the owner before it does; or it takes a service account
token the owner made. The token is kept in Dot's ``.env`` (``DOT_ONEPASSWORD_TOKEN``), which no tool of a run may read.

The shapes read here are ``op``'s JSON (``--format json``) as of its version 2: ``op vault list`` and ``op item list``
give objects with ``id`` and a ``name`` or a ``title`` (an item's ``urls`` with an ``href`` each), ``op item get``
an item with ``fields`` that say their ``purpose`` (``USERNAME``, ``PASSWORD``), and ``op item get --otp`` the code
now. Anything else is an error that says what was read, never a value."""

import asyncio
import json
import os
import re
import shutil
import socket
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from dotagent.core.errors import ToolExecutionError
from dotagent.core.logs import get_logger
from dotagent.core.paths import env_file_path
from dotagent.core.text import redact_secrets, truncate_middle
from dotagent.core.types import FrozenModel
from dotagent.secrets import parse_env_file

log = get_logger(__name__)

# Where Dot keeps the service account's token, in its .env: a name ``op`` itself does not read, so that no program
# that inherits a value of that name could hand it to ``op``.
TOKEN_NAME: str = "DOT_ONEPASSWORD_TOKEN"
# The vault Connect makes and the service account it gives Dot.
VAULT_NAME: str = "Shared with Dot"
# Where Homebrew and 1Password's installer put ``op`` when it is not on the daemon's PATH.
_CLI_PLACES: tuple[Path, ...] = (Path("/opt/homebrew/bin/op"), Path("/usr/local/bin/op"))
# A read of the vault answers in seconds; Connect waits for the owner to approve in the 1Password app.
_READ_TIMEOUT_S: float = 30.0
_CONNECT_TIMEOUT_S: float = 180.0
# A run lists the logins again within this long from the copy read: 1Password counts every read against the
# account's hourly and daily limits.
_LOGINS_FOR_S: float = 60.0
_ERROR_CHARS: int = 400
# The token a service account is given: "ops_" and Base64 URL-safe characters (1Password's security page).
TOKEN_PATTERN: re.Pattern[str] = re.compile(r"ops_[A-Za-z0-9_\-]{20,}")
# What ``op`` may read of the daemon's environment: no key or token of Dot's goes along.
_PASSED_ENV: tuple[str, ...] = ("HOME", "USER", "LOGNAME", "TMPDIR", "LANG", "LC_ALL", "PATH")


class VaultError(ToolExecutionError):
    """1Password could not be read as asked: ``op`` is missing or not signed in, the token is gone or refused, or what
    ``op`` answered is not what Dot reads. The message never holds a value of the vault."""


class _Loose(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _Vault(_Loose):
    id: str
    name: str


class _Url(_Loose):
    href: str


class _ItemOverview(_Loose):
    id: str
    title: str
    urls: list[_Url] = []


class _Field(_Loose):
    id: str = ""
    type: str = ""
    purpose: str = ""
    value: str | None = None


class _Item(_Loose):
    id: str
    title: str
    fields: list[_Field] = []


class _Account(_Loose):
    url: str = ""
    email: str = ""


class VaultView(FrozenModel):
    """A vault the token reads: its id and name, and how many logins it holds."""

    id: str
    name: str
    logins: int


class Login(FrozenModel):
    """A login a run may fill: its id, its vault's, its title and its websites (no username, no value)."""

    id: str
    vault_id: str
    title: str
    websites: tuple[str, ...]


class _Listing(FrozenModel):
    """What a token read of 1Password, and when: its vaults, each with how many logins it holds, and those logins."""

    read_at: float
    token: SecretStr
    vaults: tuple[VaultView, ...]
    logins: tuple[Login, ...]


class LoginSecret(FrozenModel):
    """What a login fills a page with, read when the run fills it: its username, password and one-time code, each
    ``None`` when the login has none or it was not asked for; never in a record (``SecretStr``)."""

    username: SecretStr | None
    password: SecretStr | None
    otp: SecretStr | None


def kept_token() -> str | None:
    """The service account's token Dot keeps, from its .env alone: the token is Dot's to keep and to forget (Disconnect
    takes it out), not one of the environment or of the Keychain. ``None`` while 1Password is not connected."""
    path: Path = env_file_path()
    if not path.exists():
        return None
    return parse_env_file(path.read_text(encoding="utf-8")).get(TOKEN_NAME)


# Where 1Password's own app is installed, which Connect goes through (item 791).
_APP_PLACES: tuple[Path, ...] = (Path("/Applications/1Password.app"), Path.home() / "Applications" / "1Password.app")


def find_app() -> Path | None:
    """1Password's app on this Mac, ``None`` when it is not installed: ``op`` alone (Homebrew's) signs in to no account,
    and Connect cannot work without it; a service account's token still can."""
    return next((place for place in _APP_PLACES if place.exists()), None)


def find_cli() -> Path | None:
    """``op`` on the daemon's PATH, or where Homebrew and 1Password's installer put it; ``None`` when it is not
    installed."""
    found: str | None = shutil.which("op")
    if found is not None:
        return Path(found)
    return next((place for place in _CLI_PLACES if place.is_file()), None)


def _environment(token: str | None) -> dict[str, str]:
    """The environment ``op`` runs in: the daemon's few that it needs, and the service account's token when it reads
    with one (without one it asks the 1Password app, which asks the owner)."""
    env: dict[str, str] = {name: value for name in _PASSED_ENV if (value := os.environ.get(name)) is not None}
    if token is not None:
        env["OP_SERVICE_ACCOUNT_TOKEN"] = token
    return env


def _read(text: str, what: str) -> object:
    """``op``'s JSON. What it answered is in no error: it may be a login's values."""
    try:
        return json.loads(text)
    except ValueError:
        raise VaultError(f"1Password's op answered {what} with something that is not JSON", {"what": what}) from None


def _parsed[T: BaseModel](kind: type[T], text: str, what: str) -> T:
    try:
        return kind.model_validate(_read(text, what))
    except ValidationError:
        # The validation error quotes the values it read: it stays out of the chain.
        raise VaultError(f"1Password's op answered {what} with something Dot does not read", {"what": what}) from None


def _parsed_list[T: BaseModel](kind: type[T], text: str, what: str) -> list[T]:
    raw: object = _read(text, what)
    if not isinstance(raw, list):
        raise VaultError(f"1Password's op answered {what} with something Dot does not read", {"what": what})
    try:
        return [kind.model_validate(entry) for entry in raw]
    except ValidationError:
        raise VaultError(f"1Password's op answered {what} with something Dot does not read", {"what": what}) from None


class OnePassword:
    """The owner's 1Password through ``op`` (``cli``, or the one found at each use when ``None``)."""

    def __init__(self, cli: Path | None) -> None:
        self._cli: Path | None = cli
        self._listing: _Listing | None = None

    def cli(self) -> Path | None:
        """The ``op`` Dot runs, or ``None`` while it is not installed."""
        return self._cli if self._cli is not None else find_cli()

    async def _op(self, args: tuple[str, ...], token: str | None, timeout_s: float) -> str:
        """What ``op args`` prints; one that fails raises with ``op``'s own words, the token never among them."""
        cli: Path | None = self.cli()
        if cli is None:
            raise VaultError(
                "1Password's command line, op, is not installed: brew install 1password-cli, then check again",
                {"looked_in": [str(place) for place in _CLI_PLACES]},
            )
        process = await asyncio.create_subprocess_exec(
            str(cli),
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_environment(token),
        )
        try:
            out, err = await asyncio.wait_for(process.communicate(), timeout_s)
        except TimeoutError as error:
            process.kill()
            await process.wait()
            raise VaultError(
                f"1Password's op {' '.join(args[:2])} did not answer within {timeout_s:.0f} seconds",
                {"command": " ".join(args[:2])},
            ) from error
        if process.returncode != 0:
            said: str = redact_secrets(err.decode("utf-8", "replace").strip())
            if token is not None:
                said = said.replace(token, "[token]")
            shown: str = truncate_middle(said, _ERROR_CHARS) or "it said nothing"
            raise VaultError(
                f"1Password's op {' '.join(args[:2])} failed: {shown}",
                {"command": " ".join(args[:2]), "exit": process.returncode},
            )
        return out.decode("utf-8", "replace")

    async def vaults(self, token: str) -> tuple[VaultView, ...]:
        """The vaults the token reads, each with how many logins it holds."""
        return (await self._listed(token)).vaults

    async def logins(self, token: str) -> tuple[Login, ...]:
        """The logins of every vault the token reads, by title."""
        return (await self._listed(token)).logins

    async def _listed(self, token: str) -> _Listing:
        """What the token reads, read again a minute at most after the copy kept."""
        now: float = time.monotonic()
        kept: _Listing | None = self._listing
        if kept is not None and kept.token.get_secret_value() == token and now - kept.read_at < _LOGINS_FOR_S:
            return kept
        vaults: list[_Vault] = _parsed_list(
            _Vault, await self._op(("vault", "list", "--format", "json"), token, _READ_TIMEOUT_S), "the vaults"
        )
        found: list[Login] = []
        for vault in vaults:
            text: str = await self._op(
                ("item", "list", "--vault", vault.id, "--categories", "Login", "--format", "json"),
                token,
                _READ_TIMEOUT_S,
            )
            found.extend(
                Login(
                    id=item.id,
                    vault_id=vault.id,
                    title=item.title,
                    websites=tuple(url.href for url in item.urls if url.href),
                )
                for item in _parsed_list(_ItemOverview, text, "the logins")
            )
        logins: tuple[Login, ...] = tuple(sorted(found, key=lambda login: login.title.lower()))
        listing: _Listing = _Listing(
            read_at=now,
            token=SecretStr(token),
            vaults=tuple(
                VaultView(id=vault.id, name=vault.name, logins=sum(1 for login in logins if login.vault_id == vault.id))
                for vault in vaults
            ),
            logins=logins,
        )
        self._listing = listing
        return listing

    async def secret(self, token: str, login: Login, otp: bool) -> LoginSecret:
        """The username and password of ``login``, and its one-time code now when ``otp``: read for a fill alone."""
        item: _Item = _parsed(
            _Item,
            await self._op(
                ("item", "get", login.id, "--vault", login.vault_id, "--format", "json"), token, _READ_TIMEOUT_S
            ),
            "the login",
        )
        username: str | None = next((one.value for one in item.fields if one.purpose == "USERNAME" and one.value), None)
        password: str | None = next((one.value for one in item.fields if one.purpose == "PASSWORD" and one.value), None)
        code: str | None = None
        if otp:
            code = (
                await self._op(("item", "get", login.id, "--vault", login.vault_id, "--otp"), token, _READ_TIMEOUT_S)
            ).strip()
            if not code:
                raise VaultError(f"the login {login.title} has no one-time code", {"login": login.id})
        return LoginSecret(
            username=None if username is None else SecretStr(username),
            password=None if password is None else SecretStr(password),
            otp=None if code is None else SecretStr(code),
        )

    async def connect(self) -> str:
        """Make the vault "Shared with Dot" when there is none, and a service account that only reads it, with the
        owner's own ``op`` signed in through the 1Password app, which asks the owner first; the service account's
        token, which ``op`` gives once."""
        accounts: list[_Account] = _parsed_list(
            _Account, await self._op(("account", "list", "--format", "json"), None, _CONNECT_TIMEOUT_S), "the accounts"
        )
        if not accounts and find_app() is None:
            # Item 791: op without 1Password's app, as Homebrew installs it, signs in to no account; the old words sent
            # the owner to the settings of an app this Mac does not have.
            raise VaultError(
                "1Password's app is not installed on this Mac, and Connect goes through it. You do not need 1Password: "
                "Dot's own vault (Settings › Vault) keeps your logins in the Mac's Keychain and fills them. To use "
                "1Password all the same, install its app, or paste a service account token made at 1password.com",
                {},
            )
        if not accounts:
            raise VaultError(
                "op is signed in to no 1Password account: in the 1Password app, turn on Settings › Developer › "
                "Integrate with 1Password CLI, then connect again",
                {},
            )
        vaults: list[_Vault] = _parsed_list(
            _Vault, await self._op(("vault", "list", "--format", "json"), None, _CONNECT_TIMEOUT_S), "the vaults"
        )
        vault: _Vault | None = next((one for one in vaults if one.name == VAULT_NAME), None)
        if vault is None:
            vault = _parsed(
                _Vault,
                await self._op(("vault", "create", VAULT_NAME, "--format", "json"), None, _CONNECT_TIMEOUT_S),
                "the new vault",
            )
        made: str = await self._op(
            (
                "service-account",
                "create",
                f"Dot on {socket.gethostname()}",
                "--vault",
                f"{vault.id}:read_items",
            ),
            None,
            _CONNECT_TIMEOUT_S,
        )
        tokens: list[str] = TOKEN_PATTERN.findall(made)
        if len(tokens) != 1:
            raise VaultError(
                "1Password's op made the service account but Dot could not read its token: make a service account that "
                f"reads {VAULT_NAME} at 1password.com and paste its token instead",
                {"tokens_found": len(tokens)},
            )
        log.info("vault_connected", vault=vault.id, accounts=len(accounts))
        return tokens[0]

    def forget(self) -> None:
        """Forget what was read: the token changed or went."""
        self._listing = None
