"""A keychain file that ``DOT_KEYCHAIN`` names and that is not there is refused before ``security`` runs (item 628):
``security`` does not fail on a keychain file that is missing, it reads and writes the login keychain in its place,
and a test's tokens went to the owner's."""

import asyncio
from pathlib import Path

import pytest

from dotagent.core.errors import SignInError
from dotagent.oauth.keychain import Keychain
from dotagent.oauth.tokens import Tokens


@pytest.fixture
def no_process(monkeypatch: pytest.MonkeyPatch) -> list[tuple[object, ...]]:
    """Every process the test would start, kept instead of started."""
    started: list[tuple[object, ...]] = []

    async def start(*arguments: object, **options: object) -> object:
        started.append(arguments)
        raise AssertionError(f"a process was started: {arguments}")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", start)
    return started


@pytest.fixture
def there(tmp_path: Path) -> str:
    """A keychain file that is there (empty: no test here gets as far as reading it)."""
    file: Path = tmp_path / "there.keychain-db"
    file.write_bytes(b"")
    return str(file)


async def test_a_keychain_file_that_is_not_there_is_refused_before_security_runs(
    tmp_path: Path, no_process: list[tuple[object, ...]]
) -> None:
    keychain: Keychain = Keychain(str(tmp_path / "missing.keychain-db"))
    tokens: Tokens = Tokens(access_token="access", refresh_token="refresh", expires_at=1.0, scope="mail")
    with pytest.raises(SignInError, match="the keychain file is not there"):
        await keychain.save("google", "someone@example.com", tokens)
    with pytest.raises(SignInError, match="the keychain file is not there"):
        await keychain.read("google", "someone@example.com")
    with pytest.raises(SignInError, match="the keychain file is not there"):
        await keychain.forget("google", "someone@example.com")
    assert no_process == []


async def test_a_keychain_file_that_is_there_is_asked(there: str, no_process: list[tuple[object, ...]]) -> None:
    with pytest.raises(AssertionError, match="a process was started"):
        await Keychain(there).read("google", "someone@example.com")
    assert no_process != []


def test_a_keychain_file_named_by_a_path_that_is_not_whole_is_refused() -> None:
    """``security`` looks for such a name under ~/Library/Keychains, the check for the file in the working folder."""
    with pytest.raises(SignInError, match="not a whole path"):
        Keychain("accounts.keychain-db")
