"""Who Dot is to its owner (item 420): the name the owner calls it by, the owner's own name, and whether Dot has
introduced itself in its first conversation, kept in ``[assistant]`` and changed live from the web UI. Until the owner
gives a name of their own, the owner's name is the first word of the full name macOS keeps for their account."""

import os
import pwd

from dotagent.config import AssistantConfig
from dotagent.core.types import FrozenModel

DEFAULT_NAME: str = "Dot"
NAME_CHARS: int = 24
OWNER_CHARS: int = 40


def first_name(full_name: str) -> str | None:
    """The first word of an account's full name as ``pw_gecos`` holds it (other fields may follow a comma), at most
    :data:`OWNER_CHARS` long; None when it has no word."""
    words: list[str] = full_name.split(",", 1)[0].split()
    return words[0][:OWNER_CHARS] if words else None


def mac_first_name() -> str | None:
    """The first name of the account of this Mac that runs the daemon; None when macOS keeps no full name for it."""
    return first_name(pwd.getpwuid(os.getuid()).pw_gecos)


def owner_name_lines(given: str, mac_name: str | None) -> tuple[str, ...]:
    """The line of the owner's name among what Dot knows about the owner: the name they gave, as their own words, else
    the first name of their account on this Mac, said to be that; none when neither is known."""
    if given != "":
        return (f"(you said) My name is {given}.",)
    if mac_name is not None:
        return (f"The user's name is {mac_name}, as their account on this Mac has it.",)
    return ()


def called_lines(name: str) -> tuple[str, ...]:
    """The line of the name the owner calls Dot by, for Dot's own runs (another agent has a name of its own); none
    while it is still Dot."""
    return () if name == DEFAULT_NAME else (f"(you said) I call you {name}: answer to that name.",)


class AssistantView(FrozenModel):
    """``name``: what the owner calls Dot; ``owner``: the name the owner gave, empty when none; ``mac_name``: the first
    name of the owner's account on this Mac, the owner's name until they give one; ``introduced``: Dot has had its
    first conversation, which the home no longer shows."""

    name: str
    owner: str
    mac_name: str | None
    introduced: bool


class Assistant:
    """The names in use now: the config's at the daemon's start, then as the owner changes them."""

    def __init__(self, config: AssistantConfig, mac_name: str | None) -> None:
        self._name: str = config.name
        self._owner: str = config.owner
        self._introduced: bool = config.introduced
        self._mac_name: str | None = mac_name

    def view(self) -> AssistantView:
        return AssistantView(name=self._name, owner=self._owner, mac_name=self._mac_name, introduced=self._introduced)

    def name(self) -> str:
        """The name the owner calls Dot by."""
        return self._name

    def owner_name(self) -> str | None:
        """The owner's name: the one they gave, else their account's first name, else None."""
        return self._owner if self._owner != "" else self._mac_name

    def rename(self, name: str) -> None:
        self._name = name

    def name_owner(self, owner: str) -> None:
        self._owner = owner

    def mark_introduced(self, introduced: bool) -> None:
        self._introduced = introduced
