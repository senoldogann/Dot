"""Dot's own vault of logins, cards and contacts (item 621), Settings › Vault: what the owner saves, what a list
shows and what a run may fill into a page (a login's username and password; a card is kept, not filled).

An item is two things kept apart. Its record is in Dot's database and holds what a list needs: for a login its website
and username, for a card its brand, its last four digits and its nickname, for a contact its fields. Its secrets are
in the owner's Keychain (:mod:`dotagent.vault.secretstore`) and nowhere else: a login's password and notes, a card's
number, expiry, security code and name. No answer of this module to a list, a view or an error holds a secret; a
secret leaves it in two ways, to the owner who presses the eye (:meth:`VaultItems.reveal`) and, for a login, to the
daemon that fills it into a page (:meth:`VaultItems.login_for_fill`), and the values given to a page are noted as
secrets Dot holds (:func:`dotagent.secrets.hold_filled`)."""

import asyncio
import contextlib
import re
import time
import unicodedata
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field, SecretStr

from dotagent.core.errors import InvalidValueError, NotFoundError
from dotagent.core.ids import new_id
from dotagent.core.logs import get_logger
from dotagent.core.types import FrozenModel
from dotagent.secrets import hold_filled
from dotagent.store.db import Store
from dotagent.store.models import VaultKind, VaultRecord
from dotagent.vault import cards
from dotagent.vault.cards import Brand, Expiry
from dotagent.vault.csvimport import (
    ALREADY_SAVED,
    CHANGED_MEANWHILE,
    FORMAT_NAMES,
    NO_ROOM,
    UNCOMPARED,
    ParsedExport,
    ParsedLogin,
    parse_export,
)
from dotagent.vault.hosts import site_label, website_problem
from dotagent.vault.secretstore import PartlyRemoved, SecretStore, VaultStoreError, fits

log = get_logger(__name__)

# The details of a contact, and of the billing address of a card, in the order the page shows them.
CONTACT_FIELDS: tuple[str, ...] = (
    "name",
    "address",
    "address2",
    "city",
    "region",
    "postal_code",
    "country",
    "phone",
    "email",
)
BILLING_FIELDS: tuple[str, ...] = ("name", "address", "address2", "city", "region", "postal_code", "country")
_FIELD_LIMITS: dict[str, int] = {
    "name": 120,
    "address": 200,
    "address2": 200,
    "city": 100,
    "region": 100,
    "postal_code": 20,
    "country": 80,
    "phone": 40,
    "email": 254,
}
# What each kind keeps in the Keychain, and so what the eye may show.
SECRET_PARTS: dict[VaultKind, tuple[str, ...]] = {
    "login": ("password", "notes"),
    "card": ("number", "expiry", "code", "name"),
    "contact": (),
}
MOST_LABEL_CHARS: int = 40
MOST_PASSWORD_CHARS: int = 512
MOST_NOTES_CHARS: int = 1000
# How many logins whose password an import changed its report names.
_NAMED_UPDATES: int = 50
# An import keeps its logins in the Keychain this many at a time, and a removal of several removes them so, so that a
# refusal part way costs one batch.
_IMPORT_BATCH: int = 40
_EMAIL: re.Pattern[str] = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_PHONE: re.Pattern[str] = re.compile(r"[0-9+\-().\s#*xX]{3,40}")
# A card's number has 12 to 19 digits, which an owner writes in groups: the name of a card or of a contact that holds a
# run of this many digits (once the spaces and hyphens between them are taken out) may hold a number. A name is listed
# to a run, which never sees the number.
_NUMBER_LIKE: re.Pattern[str] = re.compile(r"\d{12,}")
_BETWEEN_DIGITS: re.Pattern[str] = re.compile(r"[ \-.\N{NO-BREAK SPACE}\N{NARROW NO-BREAK SPACE}]")
_NO_NUMBERS: str = (
    "{what} is not for numbers: it is listed to the runs that fill the card, which never see its number. "
    "Call it Travel or Work."
)


class LoginDraft(FrozenModel):
    """A login as the owner wrote it. A part left out (``None``) is kept as it is when an item is changed; a login
    that is made needs its website and its password."""

    kind: Literal["login"]
    website: str | None = Field(default=None, max_length=2000)
    username: str | None = Field(default=None, max_length=320)
    password: SecretStr | None = Field(default=None, max_length=MOST_PASSWORD_CHARS)
    notes: SecretStr | None = Field(default=None, max_length=MOST_NOTES_CHARS)


class CardDraft(FrozenModel):
    """A card as the owner wrote it. A part left out (``None``) is kept as it is when an item is changed; an empty
    security code, name, nickname or billing address takes it away. A card that is made needs its number and expiry."""

    kind: Literal["card"]
    number: SecretStr | None = Field(default=None, max_length=64)
    expiry: SecretStr | None = Field(default=None, max_length=16)
    code: SecretStr | None = Field(default=None, max_length=8)
    name: SecretStr | None = Field(default=None, max_length=120)
    nickname: str | None = Field(default=None, max_length=MOST_LABEL_CHARS * 2)
    billing: dict[str, str] | None = None


class ContactDraft(FrozenModel):
    """A contact as the owner wrote it: a label and details."""

    kind: Literal["contact"]
    label: str | None = Field(default=None, max_length=MOST_LABEL_CHARS * 2)
    fields: dict[str, str] | None = None


ItemDraft = LoginDraft | CardDraft | ContactDraft
ITEM_DRAFT = Annotated[ItemDraft, Field(discriminator="kind")]


class Skipped(FrozenModel):
    """Rows of an import that were left out, and why."""

    reason: str
    count: int


class ImportReport(FrozenModel):
    """What an import did: the export it read, how many rows it had, how many logins went into the vault, which rows
    were left out and why, how many logins lost their notes for being too long, and ``stopped``, the reason the
    Keychain ended the import part way (``None`` when it went through)."""

    format: str
    rows: int
    imported: int
    skipped: list[Skipped]
    notes_left_out: int
    stopped: str | None
    # Logins already in the vault whose password the file has changed, and which took it (item 791), each named (site ·
    # username, the first 50): the one replaced is in its notes.
    updated: int = 0
    updated_logins: list[str] = Field(default_factory=list)


class Removal(FrozenModel):
    """What a removal of several items did: how many went, and ``stopped``, the reason the Keychain ended it part way
    (``None`` when it went through)."""

    removed: int
    stopped: str | None


class LoginFill(FrozenModel):
    """What a login gives a page: its record, and its username and password, never in a record (``SecretStr``)."""

    record: VaultRecord
    username: SecretStr | None
    password: SecretStr | None


def _single(text: str) -> str:
    """``text`` on one line, with its runs of white space made one space."""
    return " ".join(text.split())


def _holds_a_number(text: str) -> bool:
    """Whether ``text`` holds a run of 12 digits or more, however the owner groups them (``4242 4242 4242 4242``),
    written in the digits of any script (full-width ones are made ordinary first)."""
    return _NUMBER_LIKE.search(_BETWEEN_DIGITS.sub("", unicodedata.normalize("NFKC", text))) is not None


def _clean_fields(fields: Mapping[str, str], allowed: tuple[str, ...]) -> dict[str, str]:
    """The details of ``fields`` that are filled, each on one line and within its length; a name that is not among
    ``allowed``, a value too long or an email or a phone number that cannot be one is refused with what to change."""
    unknown: list[str] = sorted(set(fields) - set(allowed))
    if unknown:
        raise InvalidValueError(f"{unknown[0]!r} is not a detail the vault keeps here", {"allowed": [*allowed]})
    cleaned: dict[str, str] = {}
    for name in allowed:
        value: str = _single(fields.get(name, ""))
        if value == "":
            continue
        if len(value) > _FIELD_LIMITS[name]:
            raise InvalidValueError(f"This is too long ({_FIELD_LIMITS[name]} characters at most): {name}", {})
        if name == "email" and _EMAIL.fullmatch(value) is None:
            raise InvalidValueError("The email address does not look right.", {})
        if name == "phone" and _PHONE.fullmatch(value) is None:
            raise InvalidValueError("The phone number does not look right.", {})
        cleaned[name] = value
    return cleaned


class VaultItems:
    """Dot's own vault: the records in ``store`` and the secrets in ``secrets``. ``today`` is the date an expiry is
    judged on; ``clock`` the time an item is stamped with."""

    def __init__(
        self,
        store: Store,
        secrets: SecretStore,
        today: Callable[[], date] = date.today,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store: Store = store
        self._secrets: SecretStore = secrets
        self._today: Callable[[], date] = today
        self._clock: Callable[[], float] = clock
        # A change reads what an item keeps and writes it again: two at once would lose one of them. An item's lock
        # lasts as long as the item: it is dropped when the item is removed (and when a change finds no such item).
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, item_id: str) -> asyncio.Lock:
        return self._locks.setdefault(item_id, asyncio.Lock())

    # ----- what a list and a run are told, never a secret -------------------------------------------------------

    async def items(self) -> list[VaultRecord]:
        return await self._store.list_vault_items()

    async def item(self, item_id: str) -> VaultRecord:
        return await self._store.get_vault_item(item_id)

    async def counts(self) -> dict[str, int]:
        return await self._store.vault_counts()

    async def of_kind(self, kind: VaultKind) -> list[VaultRecord]:
        return [record for record in await self._store.list_vault_items() if record.kind == kind]

    # ----- keeping an item -----------------------------------------------------------------------------------

    async def create(self, draft: ItemDraft) -> VaultRecord:
        """Keep a new item: its secrets in the Keychain first, then its record, so that a record never stands without
        them and a Keychain that refuses leaves nothing behind."""
        item_id: str = new_id("vlt")
        now: float = self._clock()
        record: VaultRecord
        parts: dict[str, SecretStr]
        match draft:
            case LoginDraft():
                record, parts = self._login(item_id, now, now, draft, None, {})
            case CardDraft():
                record, parts = self._card(item_id, now, now, draft, None, {})
            case ContactDraft():
                record, parts = self._contact(item_id, now, now, draft, None), {}
        await self._write(record, parts, created=True)
        return record

    async def update(self, item_id: str, draft: ItemDraft) -> VaultRecord:
        """Change an item: what the draft leaves out stays as it is, in the record and in the Keychain."""
        async with self._lock(item_id):
            try:
                before: VaultRecord = await self._store.get_vault_item(item_id)
            except NotFoundError:
                self._locks.pop(item_id, None)
                raise
            if before.kind != draft.kind:
                raise InvalidValueError(
                    f"this item is a {before.kind}; it cannot be changed into a {draft.kind}", {"item_id": item_id}
                )
            now: float = self._clock()
            record: VaultRecord
            parts: dict[str, SecretStr]
            kept: dict[str, SecretStr] = await self._kept(before)
            match draft:
                case LoginDraft():
                    record, parts = self._login(item_id, before.created_at, now, draft, before, kept)
                case CardDraft():
                    record, parts = self._card(item_id, before.created_at, now, draft, before, kept)
                case ContactDraft():
                    record, parts = self._contact(item_id, before.created_at, now, draft, before), {}
            await self._write(record, parts, created=False, before=kept)
        return record

    async def remove(self, item_id: str) -> None:
        """Remove an item: its Keychain item first (one that is not there counts as removed), its record after, so
        that a Keychain that refuses leaves the item whole to be removed again."""
        async with self._lock(item_id):
            try:
                record: VaultRecord = await self._store.get_vault_item(item_id)
            except NotFoundError:
                self._locks.pop(item_id, None)
                raise
            if SECRET_PARTS[record.kind]:
                await self._secrets.remove(item_id)
            await self._store.delete_vault_item(item_id)
        self._locks.pop(item_id, None)
        log.info("vault_item_removed", kind=record.kind)

    async def remove_many(self, item_ids: Sequence[str]) -> Removal:
        """Remove several items (the ticked rows of Settings › Vault), a batch at a time: the batch's Keychain items
        first, its records after, as :meth:`remove` does. A Keychain that refuses part way ends the removal: the
        records of the items whose Keychain items went before it go too (and those that have none, a contact's), the
        others are left as they were, to be removed again, and the answer says how many went and why it stopped. An
        id the vault does not hold is passed over."""
        held: set[str] = {record.id for record in await self._store.list_vault_items()}
        wanted: list[str] = [item_id for item_id in dict.fromkeys(item_ids) if item_id in held]
        removed: int = 0
        stopped: str | None = None
        for start in range(0, len(wanted), _IMPORT_BATCH):
            batch: list[str] = sorted(wanted[start : start + _IMPORT_BATCH])
            async with contextlib.AsyncExitStack() as locks:
                for item_id in batch:
                    await locks.enter_async_context(self._lock(item_id))
                # What was removed while the locks were waited for is passed over too.
                records: list[VaultRecord] = await self._store.get_vault_items(batch)
                done: list[str] = [record.id for record in records]
                try:
                    await self._secrets.remove_many([record.id for record in records if SECRET_PARTS[record.kind]])
                except VaultStoreError as error:
                    stopped = error.message
                    gone: frozenset[str] = frozenset(error.removed if isinstance(error, PartlyRemoved) else ())
                    done = [record.id for record in records if record.id in gone or not SECRET_PARTS[record.kind]]
                if done:
                    removed += await self._store.delete_vault_items(done)
            for item_id in done:
                self._locks.pop(item_id, None)
            if stopped is not None:
                break
        log.info("vault_items_removed", items=removed, stopped=stopped is not None)
        return Removal(removed=removed, stopped=stopped)

    async def _kept(self, record: VaultRecord) -> dict[str, SecretStr]:
        """What the Keychain keeps for an item, nothing for a kind that keeps none or an item it lost."""
        if not SECRET_PARTS[record.kind]:
            return {}
        return await self._secrets.get(record.id) or {}

    async def _write(
        self,
        record: VaultRecord,
        parts: dict[str, SecretStr],
        created: bool,
        before: Mapping[str, SecretStr] | None = None,
    ) -> None:
        """Keep an item: the Keychain first, then the record. A record that cannot be kept leaves the Keychain as it
        was (``before``: what a changed item held; nothing for a new one), so that a card's label never names a number
        the Keychain no longer holds."""
        if parts:
            await self._secrets.put(record.id, parts)
        elif SECRET_PARTS[record.kind]:
            await self._secrets.remove(record.id)
        try:
            if created:
                await self._store.add_vault_items([record])
            else:
                await self._store.update_vault_item(record)
        except Exception:
            try:
                if created and parts:
                    await self._secrets.remove(record.id)
                elif before:
                    await self._secrets.put(record.id, before)
            except VaultStoreError as error:
                log.warning("vault_item_left_changed_in_keychain", error=error.message)
            raise
        log.info("vault_item_kept", kind=record.kind, created=created)

    # ----- the three kinds -----------------------------------------------------------------------------------

    def _login(
        self,
        item_id: str,
        created_at: float,
        now: float,
        draft: LoginDraft,
        before: VaultRecord | None,
        kept: Mapping[str, SecretStr],
    ) -> tuple[VaultRecord, dict[str, SecretStr]]:
        website: str = (draft.website if draft.website is not None else (before.website if before else "")) or ""
        website = website.strip()
        if website == "":
            raise InvalidValueError("A login needs the site it is for, like github.com.", {})
        problem: str | None = website_problem(website)
        if problem is not None:
            raise InvalidValueError(problem, {})
        username: str = ""
        if draft.username is not None:
            if "\n" in draft.username or "\r" in draft.username:
                raise InvalidValueError("The username is on one line.", {})
            username = _single(draft.username)
        elif before is not None:
            username = before.username or ""
        parts: dict[str, SecretStr] = dict(kept)
        if draft.password is not None:
            password: str = draft.password.get_secret_value()
            if password == "" or any(char in password for char in "\n\r\x00"):
                raise InvalidValueError("A login needs a password, on one line.", {})
            parts["password"] = draft.password
        if "password" not in parts:
            raise InvalidValueError("A login needs a password.", {})
        if draft.notes is not None:
            if draft.notes.get_secret_value() == "":
                parts.pop("notes", None)
            else:
                parts["notes"] = draft.notes
        record: VaultRecord = VaultRecord(
            id=item_id,
            kind="login",
            label=site_label(website),
            website=website,
            username=username or None,
            created_at=created_at,
            updated_at=now,
        )
        return record, parts

    def _card(
        self,
        item_id: str,
        created_at: float,
        now: float,
        draft: CardDraft,
        before: VaultRecord | None,
        kept: Mapping[str, SecretStr],
    ) -> tuple[VaultRecord, dict[str, SecretStr]]:
        parts: dict[str, SecretStr] = dict(kept)
        if draft.number is not None:
            wrong: str | None = cards.number_problem(draft.number.get_secret_value())
            digits: str | None = cards.digits_of(draft.number.get_secret_value())
            if wrong is not None or digits is None:
                raise InvalidValueError(wrong or "A card number is made of digits.", {})
            parts["number"] = SecretStr(digits)
        if draft.expiry is not None:
            said: str = draft.expiry.get_secret_value()
            problem: str | None = cards.expiry_problem(said, self._today())
            expiry: Expiry | None = cards.parse_expiry(said)
            if problem is not None or expiry is None:
                raise InvalidValueError(problem or "Write the expiry as month and year, like 08/29.", {})
            parts["expiry"] = SecretStr(cards.render_expiry(expiry))
        if draft.code is not None:
            if draft.code.get_secret_value() == "":
                parts.pop("code", None)
            else:
                wrong_code: str | None = cards.code_problem(draft.code.get_secret_value())
                if wrong_code is not None:
                    raise InvalidValueError(wrong_code, {})
                parts["code"] = draft.code
        if draft.name is not None:
            name: str = _single(draft.name.get_secret_value())
            if name == "":
                parts.pop("name", None)
            else:
                parts["name"] = SecretStr(name)
        if "number" not in parts or "expiry" not in parts:
            raise InvalidValueError("A card needs its number and its expiry.", {})
        digits_now: str = parts["number"].get_secret_value()
        brand: Brand = cards.brand_of(digits_now)
        nickname: str = ""
        if draft.nickname is not None:
            nickname = _single(draft.nickname)
        elif before is not None:
            nickname = before.nickname or ""
        if len(nickname) > MOST_LABEL_CHARS:
            raise InvalidValueError(f"The nickname is too long ({MOST_LABEL_CHARS} characters at most).", {})
        if _holds_a_number(nickname):
            raise InvalidValueError(_NO_NUMBERS.format(what="A nickname"), {})
        billing: dict[str, str] = (
            _clean_fields(draft.billing, BILLING_FIELDS)
            if draft.billing is not None
            else (before.fields if before else {})
        )
        record: VaultRecord = VaultRecord(
            id=item_id,
            kind="card",
            label=cards.card_label(nickname, brand, cards.last_four(digits_now)),
            brand=brand,
            last4=cards.last_four(digits_now),
            nickname=nickname or None,
            fields=billing,
            created_at=created_at,
            updated_at=now,
        )
        return record, parts

    def _contact(
        self, item_id: str, created_at: float, now: float, draft: ContactDraft, before: VaultRecord | None
    ) -> VaultRecord:
        label: str = _single(draft.label) if draft.label is not None else (before.label if before else "")
        if label == "":
            raise InvalidValueError("A contact needs a label, like Home or Work.", {})
        if len(label) > MOST_LABEL_CHARS:
            raise InvalidValueError(f"The label is too long ({MOST_LABEL_CHARS} characters at most).", {})
        if _holds_a_number(label):
            raise InvalidValueError(_NO_NUMBERS.format(what="A label"), {})
        details: dict[str, str] = (
            _clean_fields(draft.fields, CONTACT_FIELDS)
            if draft.fields is not None
            else (before.fields if before else {})
        )
        if not details:
            raise InvalidValueError("Fill in at least one detail: a name, an address, a phone number or an email.", {})
        return VaultRecord(
            id=item_id, kind="contact", label=label, fields=details, created_at=created_at, updated_at=now
        )

    # ----- what the owner and the daemon may read of the secrets ------------------------------------------------

    async def reveal(self, item_id: str, part: str) -> SecretStr:
        """One secret part of an item, for the owner who pressed the eye (the route answers this Mac's page alone)."""
        record: VaultRecord = await self._store.get_vault_item(item_id)
        if part not in SECRET_PARTS[record.kind]:
            raise InvalidValueError(f"a {record.kind} keeps no {part} to show", {"item_id": item_id})
        found: SecretStr | None = (await self._kept(record)).get(part)
        if found is None:
            raise NotFoundError(f"the Keychain holds no {part} for this item", {"item_id": item_id})
        return found

    async def login_for_fill(self, item_id: str) -> LoginFill:
        """What a login gives a page. Read when the run fills it, and noted as secret from then on."""
        record: VaultRecord = await self._store.get_vault_item(item_id)
        if record.kind != "login":
            raise NotFoundError("no such login in the vault", {"item_id": item_id})
        kept: dict[str, SecretStr] = await self._kept(record)
        password: SecretStr | None = kept.get("password")
        if password is None:
            raise VaultStoreError(
                f"the Keychain holds no password for the login {record.label}: edit it in Settings › Vault",
                {"item_id": item_id},
            )
        username: SecretStr | None = SecretStr(record.username) if record.username else None
        # The password is a secret from here on; the username is no secret (a site shows it once you are signed in).
        hold_filled(password.get_secret_value())
        return LoginFill(record=record, username=username, password=password)

    # ----- an import ---------------------------------------------------------------------------------------------

    async def _take_back(self, item_ids: Iterable[str]) -> None:
        """Remove from the Keychain what it keeps for items whose records were not kept. The first refusal ends it: a
        Keychain that does not answer is not asked again for each of them, and the log says how many are left."""
        remaining: list[str] = [*item_ids]
        for index, item_id in enumerate(remaining):
            try:
                await self._secrets.remove(item_id)
            except VaultStoreError as left:
                log.warning("vault_items_left_in_keychain", items=len(remaining) - index, error=left.message)
                return

    async def _renew(self, item_id: str, key: tuple[str, str], seen_at: float, login: ParsedLogin, now: float) -> str:
        """Give the saved login ``item_id`` the password of ``login`` from a file (item 791), under its lock and read
        again there: ``"updated"``, ``"same"`` when it holds that password already, ``"gone"`` when it was removed or
        changed in any way since the import began (``seen_at``, when it was last changed then: an edit of the owner's
        is never undone by a file), ``"no_room"`` when its notes cannot keep the password replaced. Its own notes stay,
        not the file's. A Keychain that does not read or write raises."""
        async with self._lock(item_id):
            try:
                before: VaultRecord = await self._store.get_vault_item(item_id)
            except NotFoundError:
                self._locks.pop(item_id, None)
                return "gone"
            if (
                before.kind != "login"
                or (site_label(before.website or ""), (before.username or "").lower()) != key
                or before.updated_at != seen_at
            ):
                return "gone"
            kept: dict[str, SecretStr] = await self._kept(before)
            old: SecretStr | None = kept.get("password")
            if old is not None and old.get_secret_value() == login.password.get_secret_value():
                return "same"
            parts: dict[str, SecretStr] = {**kept, "password": login.password}
            notes: str = (kept.get("notes") or SecretStr("")).get_secret_value()
            if old is not None:
                # A browser's file has no dates: its password may be older than the one kept (the review of item 791).
                # The one it replaces is kept in the notes, which the eye shows, so that a step back can be undone.
                stamp: str = datetime.fromtimestamp(now).strftime("%Y-%m-%d %H:%M")
                notes = f"{notes}\nPassword before the import of {stamp}: {old.get_secret_value()}".strip()
            if notes:
                parts["notes"] = SecretStr(notes)
            if len(notes) > MOST_NOTES_CHARS or not fits(parts):
                return "no_room"
            await self._write(before.model_copy(update={"updated_at": now}), parts, created=False, before=kept)
        return "updated"

    async def import_logins(self, text: str) -> ImportReport:
        """Keep the logins of a CSV export. The text is read in memory and dropped; what is left out is counted by its
        reason, and a Keychain that ends the import part way is told with how many went in."""
        parsed: ParsedExport = parse_export(text)
        # The first saved login of a site and username: one row of the file is one login, counted once in the report (a
        # second the owner saved by hand for the same pair is left as it is).
        saved: dict[tuple[str, str], VaultRecord] = {}
        for listed in await self._store.list_vault_items():
            if listed.kind == "login":
                saved.setdefault((site_label(listed.website or ""), (listed.username or "").lower()), listed)
        skipped: dict[str, int] = dict(parsed.skipped)
        wanted: list[ParsedLogin] = []
        # A login already saved is looked at once the new ones are kept (item 791): one whose password the file has
        # changed takes the new one, so that a file exported again from the browser brings the vault up to date.
        # Each with when it was last changed as the import began: one changed since is the owner's, and left alone.
        matched: list[tuple[str, tuple[str, str], ParsedLogin, float]] = []
        refused: bool = False
        for login in parsed.logins:
            key: tuple[str, str] = (site_label(login.website), login.username.lower())
            found: VaultRecord | None = saved.get(key)
            if found is None:
                wanted.append(login)
            else:
                matched.append((found.id, key, login, found.updated_at))
        now: float = self._clock()
        used: set[str] = {record.id for record in await self._store.list_vault_items()}
        imported: int = 0
        stopped: str | None = None
        for start in range(0, len(wanted), _IMPORT_BATCH):
            batch = wanted[start : start + _IMPORT_BATCH]
            records: list[VaultRecord] = []
            entries: dict[str, dict[str, SecretStr]] = {}
            for login in batch:
                item_id: str = new_id("vlt")
                while item_id in used:
                    item_id = new_id("vlt")
                used.add(item_id)
                records.append(
                    VaultRecord(
                        id=item_id,
                        kind="login",
                        label=site_label(login.website),
                        website=login.website,
                        username=login.username or None,
                        created_at=now,
                        updated_at=now,
                    )
                )
                parts: dict[str, SecretStr] = {"password": login.password}
                if login.notes is not None:
                    parts["notes"] = login.notes
                entries[item_id] = parts
            try:
                await self._secrets.put_many(entries)
            except (VaultStoreError, InvalidValueError) as error:
                refused = isinstance(error, VaultStoreError)
                # The Keychain's prompt runs its lines one by one: those before the one that failed are kept, and have
                # no record, so that nothing would list or remove them. They are taken back. An item too big for a
                # word of the prompt (``parse_export`` leaves out the notes that make it so) ends the import the same
                # way, with how many went in, and not with an error that loses what the batches before it did.
                stopped = error.message
                await self._take_back(entries)
                break
            try:
                await self._store.add_vault_items(records)
            except Exception:
                await self._take_back(entries)
                raise
            imported += len(records)
        updated: int = 0
        renewed: list[str] = []
        # Each saved login is renewed on its own, under its lock and read again there, as an edit is (the review of
        # item 791): one removed or changed meanwhile is left alone. The first Keychain that refuses or does not
        # answer ends it, and a Keychain that ended the new logins is not asked at all: the rest are counted, not lost.
        answering: bool = not refused
        for item_id, key, login, seen_at in matched:
            if not answering:
                skipped[UNCOMPARED] = skipped.get(UNCOMPARED, 0) + 1
                continue
            try:
                outcome: str = await self._renew(item_id, key, seen_at, login, now)
            except VaultStoreError as error:
                log.warning("vault_import_not_compared", error=error.message)
                answering = False
                skipped[UNCOMPARED] = skipped.get(UNCOMPARED, 0) + 1
                continue
            if outcome == "updated":
                updated += 1
                renewed.append(f"{key[0]} · {login.username}" if login.username else key[0])
            else:
                reason: str = (
                    ALREADY_SAVED if outcome == "same" else NO_ROOM if outcome == "no_room" else CHANGED_MEANWHILE
                )
                skipped[reason] = skipped.get(reason, 0) + 1
        log.info("vault_import", rows=parsed.rows, imported=imported, updated=updated, stopped=stopped is not None)
        return ImportReport(
            format=FORMAT_NAMES[parsed.format],
            rows=parsed.rows,
            imported=imported,
            skipped=[Skipped(reason=reason, count=count) for reason, count in skipped.items()],
            notes_left_out=parsed.notes_left_out,
            stopped=stopped,
            updated=updated,
            updated_logins=renewed[:_NAMED_UPDATES],
        )
