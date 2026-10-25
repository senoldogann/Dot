"""Dot's own vault (item 621), the service behind Settings › Vault: an item is a record in the database and its secrets
in the Keychain, here a stand-in for it. No record, view or error holds a secret; the Keychain is written before the
record and a refusal leaves nothing behind; the values given to a page are among the secrets Dot holds."""

import asyncio
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import pytest
import pytest_asyncio
from pydantic import SecretStr
from vault_stand_ins import MemorySecretStore, RefusingSecretStore

from dotagent import secrets
from dotagent.core.errors import InvalidValueError, NotFoundError
from dotagent.store.db import Store
from dotagent.store.models import VaultRecord
from dotagent.vault.csvimport import ParsedExport, ParsedLogin
from dotagent.vault.items import (
    CardDraft,
    ContactDraft,
    ImportReport,
    LoginDraft,
    LoginFill,
    VaultItems,
)
from dotagent.vault.secretstore import VaultStoreError

NUMBER: str = "4242 4242 4242 4242"
PASSWORD: str = "S3cret-Passw0rd!"
TODAY: date = date(2026, 10, 24)


@pytest.fixture(autouse=True)
def _own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The secrets Dot holds are read from the home folder: a throwaway one, with nothing filled yet and nothing looked
    up by another test of the process."""
    monkeypatch.setenv("DOT_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(secrets, "_filled", frozenset())
    monkeypatch.setattr(secrets, "_resolved", set())


@pytest_asyncio.fixture
async def store(tmp_path: Path) -> AsyncIterator[Store]:
    opened: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        yield opened
    finally:
        await opened.close()


def _vault(store: Store, keychain: MemorySecretStore | None = None) -> tuple[VaultItems, MemorySecretStore]:
    held: MemorySecretStore = MemorySecretStore() if keychain is None else keychain
    return VaultItems(store, held, today=lambda: TODAY), held


_SECRET_LOGIN_PARTS: tuple[str, ...] = ("password", "notes")
_SECRET_CARD_PARTS: tuple[str, ...] = ("number", "expiry", "code", "name")


def _login(**changes: str | None) -> LoginDraft:
    """A login draft; a part given as ``None`` is left out."""
    parts: dict[str, str | None] = {
        "website": "https://www.GitHub.com/login",
        "username": "ada@example.com",
        "password": PASSWORD,
        **changes,
    }
    given: dict[str, SecretStr | str] = {
        name: SecretStr(value) if name in _SECRET_LOGIN_PARTS else value
        for name, value in parts.items()
        if value is not None
    }
    return LoginDraft(kind="login", **given)  # type: ignore[arg-type]


def _card(**changes: str | dict[str, str] | None) -> CardDraft:
    """A card draft; a part given as ``None`` is left out."""
    parts: dict[str, str | dict[str, str] | None] = {
        "number": NUMBER,
        "expiry": "8/29",
        "code": "123",
        "name": "Ada Lovelace",
        "nickname": "Travel",
        **changes,
    }
    given: dict[str, SecretStr | str | dict[str, str]] = {
        name: SecretStr(value) if name in _SECRET_CARD_PARTS and isinstance(value, str) else value
        for name, value in parts.items()
        if value is not None
    }
    return CardDraft(kind="card", **given)  # type: ignore[arg-type]


async def test_a_login_is_a_record_without_its_password_and_a_keychain_item_with_it(store: Store) -> None:
    vault, keychain = _vault(store)
    made: VaultRecord = await vault.create(_login(notes="recovery code 123456"))
    assert (made.kind, made.label, made.website, made.username) == (
        "login",
        "github.com",
        "https://www.GitHub.com/login",
        "ada@example.com",
    )
    assert keychain.kept == {made.id: {"password": PASSWORD, "notes": "recovery code 123456"}}
    assert PASSWORD not in made.model_dump_json()
    assert await vault.item(made.id) == made
    assert await vault.counts() == {"login": 1, "card": 0, "contact": 0}


async def test_a_change_keeps_what_it_is_not_given_in_the_record_and_in_the_keychain(store: Store) -> None:
    vault, keychain = _vault(store)
    made: VaultRecord = await vault.create(_login(notes="a note"))
    renamed: VaultRecord = await vault.update(made.id, LoginDraft(kind="login", username="ada@work.example"))
    assert (renamed.username, renamed.website, renamed.created_at) == (
        "ada@work.example",
        made.website,
        made.created_at,
    )
    assert keychain.kept[made.id] == {"password": PASSWORD, "notes": "a note"}
    moved: VaultRecord = await vault.update(made.id, LoginDraft(kind="login", website="gitlab.com"))
    assert (moved.label, moved.username) == ("gitlab.com", "ada@work.example")
    await vault.update(made.id, LoginDraft(kind="login", password=SecretStr("another-one"), notes=SecretStr("")))
    assert keychain.kept[made.id] == {"password": "another-one"}


@pytest.mark.parametrize(
    ("draft", "said"),
    [
        (LoginDraft(kind="login", password=SecretStr("pw")), "A login needs the site it is for"),
        (LoginDraft(kind="login", website="github.com"), "A login needs a password."),
        (
            LoginDraft(kind="login", website="ftp://files.example", password=SecretStr("pw")),
            "Dot fills logins on web pages",
        ),
        (LoginDraft(kind="login", website="not a site", password=SecretStr("pw")), "Write the site's address"),
        (
            LoginDraft(kind="login", website="github", password=SecretStr("pw")),
            "does not look like the address of a site",
        ),
        (LoginDraft(kind="login", website="github.com", password=SecretStr("a\nb")), "on one line"),
        (LoginDraft(kind="login", website="github.com", username="a\nb", password=SecretStr("pw")), "on one line"),
    ],
)
async def test_a_login_that_cannot_be_filled_is_refused_in_words_and_leaves_nothing(
    store: Store, draft: LoginDraft, said: str
) -> None:
    vault, keychain = _vault(store)
    with pytest.raises(InvalidValueError, match=said):
        await vault.create(draft)
    assert keychain.kept == {} and await vault.items() == []


async def test_a_card_keeps_its_number_expiry_code_and_name_in_the_keychain_and_a_label_in_the_record(
    store: Store,
) -> None:
    vault, keychain = _vault(store)
    made: VaultRecord = await vault.create(_card())
    assert (made.kind, made.brand, made.last4, made.nickname, made.label) == (
        "card",
        "visa",
        "4242",
        "Travel",
        "Travel · Visa •••• 4242",
    )
    assert keychain.kept[made.id] == {
        "number": "4242424242424242",
        "expiry": "08/29",
        "code": "123",
        "name": "Ada Lovelace",
    }
    # The id and the times are random digits, in which a code of three digits stands by chance now and then.
    said: dict[str, object] = {
        key: value
        for key, value in made.model_dump(mode="json").items()
        if key not in {"id", "created_at", "updated_at"}
    }
    shown: str = str(said)
    for secret in ("4242424242424242", "4242 4242", "08/29", "123", "Ada Lovelace"):
        assert secret not in shown


@pytest.mark.parametrize(
    ("changes", "said"),
    [
        ({"number": "4242 4242 4242 4241"}, "does not look right"),
        ({"number": "4242 4242"}, "12 to 19 digits"),
        ({"expiry": "07/26"}, "has expired"),
        ({"expiry": "soon"}, "month and year"),
        ({"code": "12"}, "3 or 4 digits"),
        ({"number": None}, "needs its number and its expiry"),
        ({"expiry": None}, "needs its number and its expiry"),
        ({"nickname": "x" * 41}, "nickname is too long"),
    ],
)
async def test_a_card_that_is_not_good_is_refused_in_words_without_its_number(
    store: Store, changes: dict[str, str | None], said: str
) -> None:
    vault, keychain = _vault(store)
    with pytest.raises(InvalidValueError, match=said) as refused:
        await vault.create(_card(**changes))
    assert "4242" not in str(refused.value) and keychain.kept == {} and await vault.items() == []


async def test_a_card_change_keeps_the_parts_it_is_not_given_and_renames_when_the_number_changes(store: Store) -> None:
    vault, keychain = _vault(store)
    made: VaultRecord = await vault.create(_card())
    kept: VaultRecord = await vault.update(made.id, CardDraft(kind="card", nickname="Work"))
    assert (kept.label, kept.last4, kept.brand) == ("Work · Visa •••• 4242", "4242", "visa")
    assert keychain.kept[made.id]["number"] == "4242424242424242" and keychain.kept[made.id]["code"] == "123"
    amex: VaultRecord = await vault.update(made.id, CardDraft(kind="card", number=SecretStr("3782 822463 10005")))
    assert (amex.brand, amex.last4, amex.label) == ("amex", "0005", "Work · American Express •••• 0005")
    await vault.update(made.id, CardDraft(kind="card", code=SecretStr(""), name=SecretStr("")))
    assert set(keychain.kept[made.id]) == {"number", "expiry"}


async def test_a_billing_address_is_kept_with_the_card_as_a_contacts_details_are(store: Store) -> None:
    vault, _ = _vault(store)
    billing: dict[str, str] = {"address": " 12  Main St ", "city": "Izmir", "country": "Turkey", "region": ""}
    made: VaultRecord = await vault.create(_card(billing=billing))
    assert made.fields == {"address": "12 Main St", "city": "Izmir", "country": "Turkey"}
    with pytest.raises(InvalidValueError, match="'phone' is not a detail"):
        await vault.update(made.id, CardDraft(kind="card", billing={"phone": "+90 555 000 00 00"}))
    cleared: VaultRecord = await vault.update(made.id, CardDraft(kind="card", billing={}))
    assert cleared.fields == {}


async def test_a_contact_keeps_its_details_in_the_record_and_asks_the_keychain_for_nothing(store: Store) -> None:
    vault, keychain = _vault(store)
    made: VaultRecord = await vault.create(
        ContactDraft(
            kind="contact",
            label="Home",
            fields={"name": "Ada Lovelace", "address": "12 Main St", "city": "Izmir", "email": "ada@example.com"},
        )
    )
    assert made.fields == {
        "name": "Ada Lovelace",
        "address": "12 Main St",
        "city": "Izmir",
        "email": "ada@example.com",
    }
    assert keychain.calls == [] and keychain.kept == {}
    again: VaultRecord = await vault.update(
        made.id, ContactDraft(kind="contact", fields={"phone": "+90 555 000 00 00"})
    )
    assert again.fields == {"phone": "+90 555 000 00 00"} and again.label == "Home"
    await vault.remove(made.id)
    assert keychain.calls == [] and await vault.items() == []


@pytest.mark.parametrize(
    ("draft", "said"),
    [
        (ContactDraft(kind="contact", fields={"name": "Ada"}), "needs a label"),
        (ContactDraft(kind="contact", label="Home", fields={}), "at least one detail"),
        (
            ContactDraft(kind="contact", label="Home", fields={"email": "not-an-email"}),
            "email address does not look right",
        ),
        (ContactDraft(kind="contact", label="Home", fields={"phone": "call me"}), "phone number does not look right"),
        (ContactDraft(kind="contact", label="Home", fields={"hair": "red"}), "'hair' is not a detail"),
        (ContactDraft(kind="contact", label="Home", fields={"postal_code": "1" * 21}), "too long"),
        (ContactDraft(kind="contact", label="x" * 41, fields={"name": "Ada"}), "label is too long"),
    ],
)
async def test_a_contact_that_cannot_be_kept_is_refused_in_words(store: Store, draft: ContactDraft, said: str) -> None:
    vault, _ = _vault(store)
    with pytest.raises(InvalidValueError, match=said):
        await vault.create(draft)


async def test_an_item_cannot_become_another_kind(store: Store) -> None:
    vault, _ = _vault(store)
    made: VaultRecord = await vault.create(_login())
    with pytest.raises(InvalidValueError, match="this item is a login"):
        await vault.update(made.id, ContactDraft(kind="contact", label="Home", fields={"name": "Ada"}))


async def test_removing_an_item_takes_its_keychain_item_and_its_record_and_a_refusal_leaves_it_whole(
    store: Store,
) -> None:
    vault, keychain = _vault(store)
    made: VaultRecord = await vault.create(_login())
    await vault.remove(made.id)
    assert keychain.kept == {} and await vault.items() == []
    with pytest.raises(NotFoundError, match="no such item in the vault"):
        await vault.item(made.id)
    with pytest.raises(NotFoundError):
        await vault.remove(made.id)
    # A Keychain that refuses to remove leaves the record, to be removed again.
    refusing: RefusingSecretStore = RefusingSecretStore(allow=1)
    stubborn, _ = _vault(store, refusing)
    kept: VaultRecord = await stubborn.create(_login())
    with pytest.raises(VaultStoreError, match="did not remove the item"):
        await stubborn.remove(kept.id)
    assert await stubborn.item(kept.id) == kept


async def test_the_lock_of_an_item_lasts_as_long_as_the_item(store: Store) -> None:
    """A change takes a lock of its item, so that two at once do not lose one of them. The locks are not kept for
    items that are gone, nor for ids that are none: they would grow with every item ever made and every id a request
    named."""
    vault, _ = _vault(store)
    first: VaultRecord = await vault.create(_login())
    second: VaultRecord = await vault.create(_login(website="example.org"))
    await vault.update(first.id, _login(username="bob@example.com"))
    await vault.update(second.id, _login(website="example.org", username="bob@example.org"))
    assert set(vault._locks) == {first.id, second.id}
    await vault.remove(first.id)
    assert set(vault._locks) == {second.id}
    with pytest.raises(NotFoundError):
        await vault.update("vlt_never_was", _login())
    assert set(vault._locks) == {second.id}
    with pytest.raises(NotFoundError):
        await vault.remove("vlt_never_was")
    assert set(vault._locks) == {second.id}
    await vault.remove(second.id)
    assert vault._locks == {}


async def test_a_keychain_that_refuses_leaves_no_record_and_a_database_that_refuses_leaves_no_keychain_item(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    refusing, _ = _vault(store, RefusingSecretStore(allow=0))
    with pytest.raises(VaultStoreError, match="did not keep the item"):
        await refusing.create(_login())
    assert await store.list_vault_items() == []

    vault, keychain = _vault(store)

    async def failing(records: object) -> None:
        raise RuntimeError("the database is full")

    monkeypatch.setattr(store, "add_vault_items", failing)
    with pytest.raises(RuntimeError, match="database is full"):
        await vault.create(_login())
    assert keychain.kept == {}


async def test_a_record_that_cannot_be_changed_leaves_the_keychain_as_it_was(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A card's label names its last four digits: the number in the Keychain is never one the label does not name."""
    vault, keychain = _vault(store)
    made: VaultRecord = await vault.create(_card())
    kept: dict[str, str] = dict(keychain.kept[made.id])

    async def failing(record: object) -> None:
        raise RuntimeError("the database is full")

    monkeypatch.setattr(store, "update_vault_item", failing)
    with pytest.raises(RuntimeError, match="database is full"):
        await vault.update(made.id, CardDraft(kind="card", number=SecretStr("3782 822463 10005")))
    assert keychain.kept[made.id] == kept
    assert (await vault.item(made.id)).last4 == "4242"


async def test_the_eye_shows_one_secret_part_of_an_item_and_nothing_a_kind_does_not_keep(store: Store) -> None:
    vault, _ = _vault(store)
    login: VaultRecord = await vault.create(_login(notes="n"))
    card: VaultRecord = await vault.create(_card())
    contact: VaultRecord = await vault.create(ContactDraft(kind="contact", label="Home", fields={"name": "Ada"}))
    assert (await vault.reveal(login.id, "password")).get_secret_value() == PASSWORD
    assert (await vault.reveal(card.id, "number")).get_secret_value() == "4242424242424242"
    assert (await vault.reveal(card.id, "expiry")).get_secret_value() == "08/29"
    with pytest.raises(InvalidValueError, match="a login keeps no number"):
        await vault.reveal(login.id, "number")
    with pytest.raises(InvalidValueError, match="a contact keeps no password"):
        await vault.reveal(contact.id, "password")
    with pytest.raises(InvalidValueError):
        await vault.reveal(card.id, "nope")
    with pytest.raises(NotFoundError):
        await vault.reveal("vlt_missing", "password")


async def test_what_is_filled_into_a_page_is_noted_among_the_secrets_dot_holds(store: Store) -> None:
    vault, keychain = _vault(store)
    login: VaultRecord = await vault.create(_login())
    card: VaultRecord = await vault.create(_card())
    assert secrets.known_secrets(filled=True) == ()
    filled: LoginFill = await vault.login_for_fill(login.id)
    assert filled.password is not None and filled.password.get_secret_value() == PASSWORD
    assert filled.username is not None and filled.username.get_secret_value() == "ada@example.com"
    assert PASSWORD in secrets.known_secrets(filled=True)
    # What a vault filled is a mask and no more: a run that never saw it is not judged by it (item 525, T1).
    assert PASSWORD not in secrets.known_secrets(filled=False)
    # A card is kept, not filled: nothing of it is held as a secret Dot masks, and a login is asked for a login alone.
    assert "4242424242424242" not in secrets.known_secrets(filled=True) and card.kind == "card"
    # A secret that is not an item of the kind asked, or an item the Keychain lost, is said in words.
    with pytest.raises(NotFoundError, match="no such login"):
        await vault.login_for_fill(card.id)
    keychain.kept.clear()
    with pytest.raises(VaultStoreError, match=r"holds no password for the login github\.com"):
        await vault.login_for_fill(login.id)


def test_a_value_filled_goes_to_a_new_set_and_never_into_the_one_a_worker_is_reading() -> None:
    """The worker threads that mask what Dot holds iterate what was filled while a run fills another value: the set is
    replaced under a lock, never added to."""
    secrets.hold_filled("first-value", "")
    reading: frozenset[str] = secrets._filled
    secrets.hold_filled("second-value", "first-value")
    assert reading == frozenset({"first-value"})
    assert secrets._filled == frozenset({"first-value", "second-value"})
    assert secrets._filled is not reading
    assert set(secrets.known_secrets(filled=True)) >= {"first-value", "second-value"}


def _bytes_of(folder: Path) -> bytes:
    """The database file of a store and the files beside it (its write-ahead log), as they are on disk."""
    return b"".join(path.read_bytes() for path in folder.glob("dot.sqlite3*"))


async def test_what_a_database_holds_of_an_item_is_no_secret(store: Store, tmp_path: Path) -> None:
    vault, _ = _vault(store)
    await vault.create(_login(notes="recovery code 123456"))
    await vault.create(_card())
    await vault.create(ContactDraft(kind="contact", label="Home", fields={"name": "Ada Lovelace"}))
    await store.close()
    held: bytes = await asyncio.to_thread(_bytes_of, tmp_path)
    for secret in (PASSWORD, "recovery code", "4242424242424242", "4242 4242", "08/29"):
        assert secret.encode() not in held
    assert b"github.com" in held and b"Travel" in held and b"Ada Lovelace" in held


CHROME: str = "name,url,username,password,note\n" + "".join(
    f"s{n},https://site{n}.example,user{n},pw-{n},\n" for n in range(95)
)


async def test_an_import_keeps_the_logins_in_batches_and_counts_what_it_left_out(store: Store) -> None:
    vault, keychain = _vault(store)
    await vault.create(_login(website="https://site3.example", username="user3"))
    report: ImportReport = await vault.import_logins(CHROME + ",,,,\nbad,,u,pw,\n")
    assert (report.format, report.rows, report.imported, report.stopped) == ("Chrome or Edge", 96, 94, None)
    assert {(skip.reason, skip.count) for skip in report.skipped} == {
        ("no website", 1),
        ("already in the vault, or twice in the file", 1),
    }
    assert keychain.calls.count("put_many") == 3
    logins = await vault.of_kind("login")
    assert len(logins) == 95 and len({login.id for login in logins}) == 95
    assert keychain.kept[next(login.id for login in logins if login.username == "user7")] == {"password": "pw-7"}
    again: ImportReport = await vault.import_logins(CHROME)
    assert again.imported == 0 and again.skipped[0].count == 95


class _StopsPartWay(MemorySecretStore):
    """A Keychain whose prompt runs the lines of a batch one by one and stops at the one after ``keep``: those before it
    are kept, as ``security -i`` keeps them."""

    def __init__(self, keep: int, removing: bool = True) -> None:
        super().__init__()
        self._keep: int = keep
        self._removing: bool = removing

    async def put_many(self, entries):  # type: ignore[no-untyped-def]
        self.calls.append("put_many")
        for index, (account, parts) in enumerate(entries.items()):
            if self._keep == 0 or index >= self._keep:
                raise VaultStoreError("the Keychain did not keep the items", {"returncode": 1})
            self.kept[account] = {name: value.get_secret_value() for name, value in parts.items()}
        self._keep -= len(entries)

    async def remove(self, account: str) -> bool:
        if not self._removing:
            self.calls.append("remove")
            raise VaultStoreError("the Keychain did not remove the item", {"returncode": 1})
        return await super().remove(account)


async def test_an_import_that_stops_part_way_through_a_batch_takes_back_what_the_prompt_kept(store: Store) -> None:
    """A batch's lines are kept one by one: the ones before the failing line are kept and have no record, so that
    nothing lists or removes them. They are taken back, and the Keychain holds an item only for each record."""
    vault, keychain = _vault(store, _StopsPartWay(keep=47))
    report: ImportReport = await vault.import_logins(CHROME)
    assert (report.imported, report.stopped) == (40, "the Keychain did not keep the items")
    records = {record.id for record in await vault.of_kind("login")}
    assert len(records) == 40 and set(keychain.kept) == records
    assert keychain.calls.count("remove") == 40


async def test_a_keychain_that_will_not_remove_is_asked_once_and_the_import_still_says_where_it_stopped(
    store: Store,
) -> None:
    vault, keychain = _vault(store, _StopsPartWay(keep=47, removing=False))
    report: ImportReport = await vault.import_logins(CHROME)
    assert (report.imported, report.stopped) == (40, "the Keychain did not keep the items")
    assert keychain.calls.count("remove") == 1
    assert len(await vault.of_kind("login")) == 40 and len(keychain.kept) == 47


async def test_an_import_whose_records_cannot_be_kept_takes_the_keychain_items_back(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault, keychain = _vault(store)

    async def refusing(records: list[VaultRecord]) -> None:
        raise RuntimeError("the database refused")

    monkeypatch.setattr(store, "add_vault_items", refusing)
    with pytest.raises(RuntimeError, match="the database refused"):
        await vault.import_logins(CHROME)
    assert keychain.kept == {} and keychain.calls.count("remove") == 40


@pytest.mark.parametrize(
    "number",
    [
        "4242 4242 4242 4242",
        "Mum 4242-4242-4242-4242",
        "4242424242424242",
        "card 424242424242 x",
        "4242.4242.4242.4242",
        "\N{FULLWIDTH DIGIT FOUR}\N{FULLWIDTH DIGIT TWO}" * 8,
        "\N{ARABIC-INDIC DIGIT FOUR}\N{ARABIC-INDIC DIGIT TWO}" * 8,
    ],
)
async def test_a_nickname_or_a_label_that_holds_a_card_number_is_refused_in_words(store: Store, number: str) -> None:
    """A run is told a card's nickname and a contact's label: neither holds a number."""
    vault, keychain = _vault(store)
    with pytest.raises(InvalidValueError, match="A nickname is not for numbers") as refused:
        await vault.create(_card(nickname=number))
    assert "4242" not in str(refused.value)
    with pytest.raises(InvalidValueError, match="A label is not for numbers"):
        await vault.create(ContactDraft(kind="contact", label=number, fields={"name": "Ada"}))
    made: VaultRecord = await vault.create(_card(nickname="Travel"))
    with pytest.raises(InvalidValueError, match="A nickname is not for numbers"):
        await vault.update(made.id, CardDraft(kind="card", nickname=number))
    assert (await vault.item(made.id)).nickname == "Travel" and len(keychain.kept) == 1
    assert await vault.of_kind("contact") == []


async def test_a_name_with_a_year_or_a_short_number_in_it_is_no_number(store: Store) -> None:
    vault, _ = _vault(store)
    made: VaultRecord = await vault.create(_card(nickname="Travel 2026 2027 and 10 24"))
    assert made.nickname == "Travel 2026 2027 and 10 24"
    kept: VaultRecord = await vault.create(
        ContactDraft(kind="contact", label="Home 12", fields={"phone": "+90 555 123 45 67"})
    )
    assert kept.fields == {"phone": "+90 555 123 45 67"}


async def test_an_import_stops_where_the_keychain_refuses_and_says_how_many_went_in(store: Store) -> None:
    vault, keychain = _vault(store, RefusingSecretStore(allow=1))
    report: ImportReport = await vault.import_logins(CHROME)
    assert (report.imported, report.stopped) == (40, "the Keychain did not keep the items")
    assert len(await vault.of_kind("login")) == 40 and len(keychain.kept) == 40
    for text in (report.model_dump_json(), str(report.stopped)):
        assert "pw-" not in text


async def test_an_import_keeps_a_login_whose_notes_are_short_in_characters_and_too_much_for_the_keychain(
    store: Store,
) -> None:
    """Pre-push review, round 5: the notes of a row passed the limit in characters (600 emoji) and not the Keychain's
    (3,000 characters of base64), so the second batch of an import raised after the first was kept, and the owner was
    told neither how many went in nor which row. The notes are left out and counted, and every login goes in."""
    vault, keychain = _vault(store)
    emoji: str = "\N{GRINNING FACE}" * 600
    text: str = "name,url,username,password,note\n" + "".join(
        f"s{n},https://site{n}.example,user{n},pw-{n},{emoji if n == 42 else ''}\n" for n in range(45)
    )
    report: ImportReport = await vault.import_logins(text)
    assert (report.rows, report.imported, report.stopped, report.notes_left_out) == (45, 45, None, 1)
    logins = await vault.of_kind("login")
    assert len(logins) == 45 and keychain.calls.count("put_many") == 2
    assert keychain.kept[next(login.id for login in logins if login.username == "user42")] == {"password": "pw-42"}


async def test_an_import_stopped_by_an_item_too_big_for_the_keychain_is_told_with_how_many_went_in(
    store: Store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If an item is too big all the same, the import ends as one that a Keychain refused does (a report, with the
    batches before it kept), and not as an error that loses what they did."""
    vault, keychain = _vault(store)
    logins: tuple[ParsedLogin, ...] = tuple(
        ParsedLogin(
            website=f"https://site{n}.example",
            username=f"user{n}",
            password=SecretStr(f"pw-{n}"),
            notes=SecretStr("\N{GRINNING FACE}" * 2000) if n == 42 else None,
        )
        for n in range(45)
    )
    monkeypatch.setattr(
        "dotagent.vault.items.parse_export",
        lambda text: ParsedExport(format="chrome", logins=logins, rows=45, skipped={}, notes_left_out=0),
    )
    report: ImportReport = await vault.import_logins("any")
    assert report.imported == 40 and report.stopped is not None and "too much text" in report.stopped
    assert len(await vault.of_kind("login")) == 40 and len(keychain.kept) == 40


async def test_a_file_that_is_no_list_of_logins_changes_nothing(store: Store) -> None:
    vault, keychain = _vault(store)
    with pytest.raises(InvalidValueError, match="not a list of logins"):
        await vault.import_logins("a,b\n1,2\n")
    assert keychain.calls == [] and await vault.items() == []
