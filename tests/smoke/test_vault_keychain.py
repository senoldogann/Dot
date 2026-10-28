"""The Keychain behind Dot's vault (item 621), as the Mac's own ``security`` keeps it, in a keychain file of the
test's own (the owner's login keychain is never asked): what is written goes in through the prompt's standard input and
never into an argument list; every part of an item comes back; a value with quotes, spaces, backslashes, accents and a
line break is kept as it was; an update replaces; a removal removes; an item the keychain lacks is none. The accounts'
tokens are kept the same way (``test_oauth_accounts.py``)."""

import asyncio
import re
import subprocess
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from pydantic import SecretStr

from dotagent.core.errors import InvalidValueError
from dotagent.vault.secretstore import SERVICE, LoginKeychain, VaultStoreError, decode_parts, encode_parts

pytestmark = pytest.mark.asyncio

_SECURITY: str = "/usr/bin/security"
_ACCOUNT: re.Pattern[str] = re.compile(r'"acct"<blob>="([^"]+)"')
AWKWARD: str = "pa ss\"w'ord\\ with\nbreak ü ışık 日本"


def _items(file: str) -> list[str]:
    """The accounts of the items of the vault that ``file`` holds, sorted."""
    said: str = subprocess.run([_SECURITY, "dump-keychain", file], check=True, capture_output=True, text=True).stdout
    return sorted(_ACCOUNT.findall(said))


def _parts(**given: str) -> dict[str, SecretStr]:
    return {name: SecretStr(value) for name, value in given.items()}


async def test_several_are_removed_in_one_prompt_and_one_the_keychain_lacks_does_not_stop_the_rest(
    keychain_file: str,
) -> None:
    """The Delete of the ticked rows of Settings › Vault (item 768): a removal of several items, one of which the
    keychain does not hold, leaves none of the others behind and the items not named as they were."""
    keychain: LoginKeychain = LoginKeychain(keychain_file)
    await keychain.put_many({f"vlt_many_{n}": _parts(password=f"pw-{n}") for n in range(4)})
    await keychain.remove_many(["vlt_many_0", "vlt_many_1"])
    assert _items(keychain_file) == ["vlt_many_2", "vlt_many_3"]
    # The account the keychain lacks comes first: its line fails, and the one after it is removed all the same.
    await keychain.remove_many(["vlt_not_there", "vlt_many_2"])
    assert _items(keychain_file) == ["vlt_many_3"]
    await keychain.remove_many([])
    assert _items(keychain_file) == ["vlt_many_3"]


async def test_what_is_kept_comes_back_whole_and_a_removed_item_is_none(keychain_file: str) -> None:
    keychain: LoginKeychain = LoginKeychain(keychain_file)
    await keychain.put("vlt_login_1", _parts(password=AWKWARD, notes="a note"))
    await keychain.put("vlt_card_1", _parts(number="4242424242424242", expiry="08/39", code="737", name="Ada Lovelace"))
    assert _items(keychain_file) == ["vlt_card_1", "vlt_login_1"]
    login = await keychain.get("vlt_login_1")
    assert login is not None and {name: value.get_secret_value() for name, value in login.items()} == {
        "password": AWKWARD,
        "notes": "a note",
    }
    card = await keychain.get("vlt_card_1")
    assert card is not None and card["number"].get_secret_value() == "4242424242424242"
    # A second put replaces what the item had.
    await keychain.put("vlt_login_1", _parts(password="another"))
    replaced = await keychain.get("vlt_login_1")
    assert (
        replaced is not None and list(replaced) == ["password"] and replaced["password"].get_secret_value() == "another"
    )
    assert _items(keychain_file) == ["vlt_card_1", "vlt_login_1"]
    assert await keychain.remove("vlt_login_1") is True
    assert await keychain.get("vlt_login_1") is None
    assert await keychain.remove("vlt_login_1") is False
    assert _items(keychain_file) == ["vlt_card_1"]
    assert await keychain.remove("vlt_card_1") is True


async def test_many_items_go_in_with_one_run_of_the_prompt(keychain_file: str) -> None:
    keychain: LoginKeychain = LoginKeychain(keychain_file)
    entries = {f"vlt_import_{n}": _parts(password=f"pw-{n}") for n in range(30)}
    await keychain.put_many(entries)
    assert _items(keychain_file) == sorted(entries)
    last = await keychain.get("vlt_import_29")
    assert last is not None and last["password"].get_secret_value() == "pw-29"
    await keychain.put_many({})


async def test_an_item_the_keychain_lacks_a_name_it_cannot_take_and_a_part_too_long_are_told_in_words(
    keychain_file: str,
) -> None:
    keychain: LoginKeychain = LoginKeychain(keychain_file)
    assert await keychain.get("vlt_nobody") is None
    for account in ("has space", "has'quote", "a;b", "", "x" * 101):
        with pytest.raises(VaultStoreError, match="id the Keychain cannot name"):
            await keychain.put(account, _parts(password="pw"))
    with pytest.raises(InvalidValueError, match="too much text to keep in the Keychain"):
        await keychain.put("vlt_long", _parts(password="p" * 512, notes="n" * 3500))
    assert _items(keychain_file) == []
    with pytest.raises(VaultStoreError, match="has a space, a quote or a backslash"):
        LoginKeychain("/tmp/has space/x.keychain-db")


async def test_the_value_is_on_no_command_line_while_security_runs(
    keychain_file: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every argument list the daemon hands to ``security``, as the process is started, holds no part of a value."""
    started: list[tuple[str, ...]] = []
    real = asyncio.create_subprocess_exec

    async def watching(program: str, *arguments: str, **options: object):  # type: ignore[no-untyped-def]
        started.append((program, *arguments))
        return await real(program, *arguments, **options)  # type: ignore[arg-type]

    monkeypatch.setattr("asyncio.create_subprocess_exec", watching)
    keychain: LoginKeychain = LoginKeychain(keychain_file)
    secret: str = "unmistakable-secret-value-7781"
    await keychain.put("vlt_watched", _parts(password=secret))
    await keychain.get("vlt_watched")
    await keychain.remove("vlt_watched")
    assert started, "security was started"
    word: str = encode_parts(_parts(password=secret))
    for command in started:
        for argument in command:
            assert secret not in argument and word not in argument, command
    assert started[0][1:] == ("-i",)


async def test_a_keychain_file_that_is_not_there_is_refused_and_security_is_never_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``security`` given a keychain file that does not exist does not fail: it keeps, reads and removes the item in the
    owner's login keychain in its place (found out when a probe left an item there). The adapter refuses such a file
    before it asks anything, and this test starts no process at all: a start fails it."""

    async def never(*arguments: str, **options: object) -> asyncio.subprocess.Process:
        raise AssertionError(f"a process was started: {arguments[:2]}")

    monkeypatch.setattr("asyncio.create_subprocess_exec", never)
    missing: str = str(tmp_path / "nowhere" / "vault.keychain-db")
    keychain: LoginKeychain = LoginKeychain(missing)
    asks: list[Callable[[], Awaitable[object]]] = [
        lambda: keychain.put("vlt_x", _parts(password="pw")),
        lambda: keychain.put_many({"vlt_y": _parts(password="pw"), "vlt_z": _parts(password="pw")}),
        lambda: keychain.get("vlt_x"),
        lambda: keychain.remove("vlt_x"),
    ]
    for ask in asks:
        with pytest.raises(VaultStoreError, match="keychain file is not there") as refused:
            await ask()
        assert refused.value.context == {"file": missing}
        assert "login keychain" in str(refused.value)


class _FakeSecurity:
    """What ``security`` answers when its keychain refuses: an exit status and what it says on standard error."""

    def __init__(self, returncode: int, said: str) -> None:
        self.returncode: int | None = returncode
        self._said: bytes = said.encode()

    async def communicate(self, given: bytes | None = None) -> tuple[bytes, bytes]:
        return b"", self._said


async def test_a_keychain_that_refuses_says_what_the_owner_can_do_and_none_of_a_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A locked login keychain answers nothing to a program with nobody to ask: the words say to unlock it and to answer
    its prompt, and what ``security`` said is in them with every run that could be a value in base64 cut out. An item
    that is not there (exit status 44) is still none, not a refusal."""
    stand_in: Path = tmp_path / "stand-in.keychain-db"
    await asyncio.to_thread(stand_in.write_bytes, b"")
    answers: list[tuple[int, str]] = []
    secret: str = "unmistakable-secret-value-7781"
    word: str = encode_parts(_parts(password=secret))

    async def fake(*arguments: str, **options: object) -> _FakeSecurity:
        return _FakeSecurity(*answers.pop())

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake)
    keychain: LoginKeychain = LoginKeychain(str(stand_in))
    refusal: str = f"security: SecKeychainItemCreateFromContent: User interaction is not allowed. {word}"
    asks: list[Callable[[], Awaitable[object]]] = [
        lambda: keychain.put("vlt_x", _parts(password=secret)),
        lambda: keychain.put_many({"vlt_x": _parts(password=secret)}),
        lambda: keychain.get("vlt_x"),
        lambda: keychain.remove("vlt_x"),
    ]
    for ask in asks:
        answers.append((36, refusal))
        with pytest.raises(VaultStoreError, match="login keychain is unlocked") as refused:
            await ask()
        said: object = refused.value.context["said"]
        assert isinstance(said, str) and "User interaction is not allowed" in said
        assert word not in said and word not in str(refused.value) and secret not in str(refused.value)
    answers.append((44, "security: the specified item could not be found in the keychain."))
    assert await keychain.get("vlt_x") is None
    answers.append((44, "security: the specified item could not be found in the keychain."))
    assert await keychain.remove("vlt_x") is False
    assert answers == []


async def test_a_part_is_one_word_of_base64_that_reads_back_and_a_word_that_is_none_is_refused() -> None:
    word: str = encode_parts(_parts(password=AWKWARD, notes="n"))
    assert re.fullmatch(r"[A-Za-z0-9_\-=]+", word) is not None
    assert {name: value.get_secret_value() for name, value in decode_parts(word).items()} == {
        "password": AWKWARD,
        "notes": "n",
    }
    for bad in ("", "not base64 at all!", "bm90IGpzb24=", "WzFd", "eyJhIjogMX0="):
        with pytest.raises(VaultStoreError, match="could not be read") as refused:
            decode_parts(bad)
        assert bad == "" or bad not in str(refused.value)
    assert SERVICE == "dot-vault"
