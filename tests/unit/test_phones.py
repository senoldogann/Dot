"""The phones paired with Dot (item 670): each holds a key of its own, which the guard takes while the phone is listed.

The daemon keeps the key's hash, never the key, in a file only the owner reads; a file it cannot read is refused rather
than taken as no phones; a key is compared in constant time; twenty phones at most; a phone's last use is written at
most every ten minutes; a removed phone's event is set, so that its open streams end. The guard refuses a phone's key
where the installation's token alone is taken (pairing codes and the paired phones), and lets it in elsewhere.
"""

import hashlib
import json
import stat
from pathlib import Path

import pytest

from dotagent.core.errors import ConfigError
from dotagent.server.guard import Refusal, check_request, needs_main_token
from dotagent.server.phones import (
    LAST_USED_EVERY_S,
    MAX_PHONES,
    PairedPhones,
    phone_id_of,
    phone_name,
)

PORT: int = 8765
TOKEN: str = "MARKER-0123456789abcdef"
LOCAL_HOST: str = f"127.0.0.1:{PORT}"


def _kept(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _lock(folder: Path) -> None:
    folder.chmod(0o500)


def _unlock(folder: Path) -> None:
    folder.chmod(0o700)


async def test_a_pairing_gives_a_key_whose_hash_alone_is_kept_and_which_the_guard_takes(tmp_path: Path) -> None:
    path: Path = tmp_path / "phones.json"
    phones: PairedPhones = PairedPhones.load(path)
    phone, key = await phones.pair("Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X)", 1000.0)
    assert phone_id_of(key) == phone.id and phone.name == "iPhone"
    kept: str = _kept(path)
    assert key not in kept and hashlib.sha256(key.encode()).hexdigest() in kept
    assert _mode(path) == 0o600
    assert phones.authenticate(key) == phone.id
    # Another secret under the same id, a key cut short, the token's shape and nothing: none is a phone.
    assert phones.authenticate(f"phone.{phone.id}.{'A' * 43}") is None
    assert phones.authenticate(key[:-1]) is None
    assert phones.authenticate(f"{key}\n") is None and phone_id_of(f"{key}\n") is None
    assert phones.authenticate(TOKEN) is None
    assert phones.authenticate(None) is None
    # Read again from the file, the phone is still there, and its key still works.
    again: PairedPhones = PairedPhones.load(path)
    assert [listed.id for listed in again.listed()] == [phone.id] and again.authenticate(key) == phone.id


async def test_a_removed_phone_is_refused_and_its_streams_are_told(tmp_path: Path) -> None:
    phones: PairedPhones = PairedPhones.load(tmp_path / "phones.json")
    phone, key = await phones.pair(None, 1000.0)
    removed = phones.removed(phone.id)
    assert not removed.is_set()
    assert await phones.remove(phone.id)
    assert removed.is_set() and phones.authenticate(key) is None and phones.proof_key(phone.id) is None
    assert not await phones.remove(phone.id)
    # A phone that is not listed counts as removed from the start.
    assert phones.removed("0123456789ab").is_set()
    assert PairedPhones.load(tmp_path / "phones.json").listed() == []


async def test_twenty_phones_at_most(tmp_path: Path) -> None:
    phones: PairedPhones = PairedPhones.load(tmp_path / "phones.json")
    for _ in range(MAX_PHONES):
        await phones.pair(None, 1000.0)
    with pytest.raises(ConfigError, match="remove one in Settings › Phone"):
        await phones.pair(None, 1000.0)
    assert len(phones.listed()) == MAX_PHONES


async def test_a_phones_last_use_is_written_at_most_every_ten_minutes(tmp_path: Path) -> None:
    path: Path = tmp_path / "phones.json"
    phones: PairedPhones = PairedPhones.load(path)
    phone, _ = await phones.pair(None, 1000.0)
    await phones.used(phone.id, 2000.0)
    await phones.used(phone.id, 2000.0 + LAST_USED_EVERY_S - 1)
    assert json.loads(_kept(path))[0]["last_used_at"] == 2000.0
    await phones.used(phone.id, 2000.0 + LAST_USED_EVERY_S)
    assert json.loads(_kept(path))[0]["last_used_at"] == 2000.0 + LAST_USED_EVERY_S


@pytest.mark.parametrize(
    "text",
    [
        "{}",
        "not json",
        '[{"id": "x", "name": "iPhone", "paired_at": 1, "last_used_at": null, "key_sha256": "' + "0" * 64 + '"}]',
        '[{"id": "0123456789ab", "name": "iPhone", "paired_at": 1, "last_used_at": null, "key_sha256": "short"}]',
        '[{"id": "0123456789ab"}]',
    ],
)
def test_a_file_that_cannot_be_read_is_refused_not_taken_as_no_phones(tmp_path: Path, text: str) -> None:
    path: Path = tmp_path / "phones.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError, match="is not a list of paired phones"):
        PairedPhones.load(path)


@pytest.mark.parametrize(
    ("agent", "name"),
    [
        ("Mozilla/5.0 (iPad; CPU OS 18_7 like Mac OS X)", "iPad"),
        ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X)", "iPhone"),
        ("Mozilla/5.0 (Linux; Android 15; Pixel 9)", "Android phone"),
        (None, "Phone"),
    ],
)
def test_a_phone_is_named_from_the_browser_it_paired_with(agent: str | None, name: str) -> None:
    assert phone_name(agent) == name


@pytest.mark.parametrize(
    ("path", "only_the_token"),
    [
        ("/api/phone/pairing", True),
        ("/api/phone/phones", True),
        ("/api/phone/phones/0123456789ab", True),
        ("/api/channels/imessage/switch", True),
        ("/api/channels/imessage/code", True),
        ("/api/channels/imessage/confirm", True),
        ("/api/channels/imessage/remove", True),
        ("/api/channels", False),
        ("/api/channels/imessage/poll", False),
        ("/api/phone/pair", False),
        ("/api/phone", False),
        ("/api/phone/phonesx", False),
        ("/api/runs", False),
    ],
)
def test_a_phones_key_is_refused_where_the_token_alone_is_taken(path: str, only_the_token: bool) -> None:
    assert needs_main_token(path) is only_the_token
    phone_key: str = f"Bearer phone.0123456789ab.{'A' * 43}"
    refusal: Refusal | None = check_request(
        path, LOCAL_HOST, None, None, None, phone_key, TOKEN, PORT, frozenset(), "0123456789ab"
    )
    if path == "/api/phone/pair":
        assert refusal is None
    elif only_the_token:
        assert refusal is not None and refusal.status == 403 and "do it on your Mac" in refusal.message
    else:
        assert refusal is None
    # Without a listed phone, the same key is no token at all; the token is taken everywhere.
    unlisted: Refusal | None = check_request(path, LOCAL_HOST, None, None, None, phone_key, TOKEN, PORT, frozenset())
    assert (unlisted is not None and unlisted.status == 401) or path == "/api/phone/pair"
    assert check_request(path, LOCAL_HOST, None, None, None, f"Bearer {TOKEN}", TOKEN, PORT, frozenset()) is None


async def test_a_last_use_that_cannot_be_written_keeps_no_phone_out(tmp_path: Path) -> None:
    folder: Path = tmp_path
    phones: PairedPhones = PairedPhones.load(folder / "phones.json")
    phone, key = await phones.pair(None, 1000.0)
    _lock(folder)
    try:
        await phones.used(phone.id, 2000.0)
    finally:
        _unlock(folder)
    assert phones.authenticate(key) == phone.id and phones.listed()[0].last_used_at is None
