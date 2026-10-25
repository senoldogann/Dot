"""The logins a run may fill (item 621): the logins of the owner's 1Password (item 493) beside those of Dot's own
vault, each found by its id or its title. A run is told them by id and title, never by a value."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from dotagent.core.errors import ToolInputError
from dotagent.store.models import VaultRecord
from dotagent.vault.hosts import website_hosts
from dotagent.vault.onepassword import Login

type LoginSource = Literal["1password", "dot"]

SOURCE_NAMES: dict[LoginSource, str] = {"1password": "1Password", "dot": "Dot's vault"}


@dataclass(frozen=True)
class LoginChoice:
    """A login a run may fill: its id, the title it is listed by, its websites, where it is kept and, for a login of
    1Password, the login itself (which its fill reads by)."""

    id: str
    title: str
    websites: tuple[str, ...]
    source: LoginSource
    onepassword: Login | None
    username: str | None

    @property
    def source_name(self) -> str:
        return SOURCE_NAMES[self.source]

    @property
    def hosts(self) -> tuple[str, ...]:
        return website_hosts(self.websites)


def login_choices(onepassword: Sequence[Login], records: Sequence[VaultRecord]) -> list[LoginChoice]:
    """The logins of 1Password and of Dot's vault, one list. A login of the vault is titled by its site, with its
    username when it has one (``github.com · ada@example.com``), so that two of one site are told apart."""
    chosen: list[LoginChoice] = [
        LoginChoice(
            id=login.id,
            title=login.title,
            websites=login.websites,
            source="1password",
            onepassword=login,
            username=None,
        )
        for login in onepassword
    ]
    chosen.extend(
        LoginChoice(
            id=record.id,
            title=f"{record.label} · {record.username}" if record.username else record.label,
            websites=(record.website,) if record.website else (),
            source="dot",
            onepassword=None,
            username=record.username,
        )
        for record in records
        if record.kind == "login"
    )
    return chosen


def find_login(choices: Sequence[LoginChoice], wanted: str) -> LoginChoice:
    """The login ``wanted`` names, by its id or its title (any case); refused for one that is not among ``choices``, and
    for a title two logins share, which only an id tells apart."""
    by_id: LoginChoice | None = next((login for login in choices if login.id == wanted), None)
    titled: list[LoginChoice] = [login for login in choices if login.title.lower() == wanted.lower()]
    if by_id is None and len(titled) > 1:
        raise ToolInputError(
            f"{len(titled)} logins are called {wanted!r}: name one by its id, as browser_logins lists it",
            {"login": wanted, "ids": [login.id for login in titled]},
        )
    found: LoginChoice | None = by_id if by_id is not None else (titled[0] if titled else None)
    if found is None:
        raise ToolInputError(
            f"there is no login {wanted!r} that Dot may fill (no such item): browser_logins lists them",
            {"login": wanted, "logins": len(choices)},
        )
    return found
