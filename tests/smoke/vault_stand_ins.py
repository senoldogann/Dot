"""Stand-ins for the Keychain behind Dot's vault (item 621), so that no test creates, reads or removes an item of the
owner's. ``MemorySecretStore`` keeps the parts in a dictionary, through the same encoding the real one writes (so a part
that is too long is refused here as it is there); ``RefusingSecretStore`` is a Keychain that refuses, locked or with a
prompt nobody answers."""

from collections.abc import Mapping

from pydantic import SecretStr

from dotagent.vault.secretstore import Parts, VaultStoreError, encode_parts


class MemorySecretStore:
    """The Keychain as a dictionary: ``kept`` is account to parts, as plain text, for a test to look into; ``calls`` the
    names of the calls made, in order."""

    def __init__(self) -> None:
        self.kept: dict[str, dict[str, str]] = {}
        self.calls: list[str] = []

    async def put(self, account: str, parts: Parts) -> None:
        self.calls.append("put")
        encode_parts(parts)
        self.kept[account] = {name: value.get_secret_value() for name, value in parts.items()}

    async def put_many(self, entries: Mapping[str, Parts]) -> None:
        self.calls.append("put_many")
        for account, parts in entries.items():
            encode_parts(parts)
            self.kept[account] = {name: value.get_secret_value() for name, value in parts.items()}

    async def get(self, account: str) -> dict[str, SecretStr] | None:
        self.calls.append("get")
        found: dict[str, str] | None = self.kept.get(account)
        return None if found is None else {name: SecretStr(value) for name, value in found.items()}

    async def remove(self, account: str) -> bool:
        self.calls.append("remove")
        return self.kept.pop(account, None) is not None


class RefusingSecretStore(MemorySecretStore):
    """A Keychain that refuses to keep, give or remove anything after ``allow`` more writes."""

    def __init__(self, allow: int = 0) -> None:
        super().__init__()
        self._allow: int = allow

    async def put(self, account: str, parts: Parts) -> None:
        if self._allow <= 0:
            raise VaultStoreError("the Keychain did not keep the item", {"returncode": 51})
        self._allow -= 1
        await super().put(account, parts)

    async def put_many(self, entries: Mapping[str, Parts]) -> None:
        if self._allow <= 0:
            raise VaultStoreError("the Keychain did not keep the items", {"returncode": 51})
        self._allow -= 1
        await super().put_many(entries)

    async def remove(self, account: str) -> bool:
        raise VaultStoreError("the Keychain did not remove the item", {"returncode": 51})
