"""The phones paired with Dot (item 670): each holds a key of its own, which the guard takes beside the installation's
token, and which Settings › Phone lists and removes.

A key is ``phone.<id>.<secret>``: the id names the phone, the secret is 32 random bytes. The daemon keeps the SHA-256
of the key, never the key, in ``<home>/phones.json`` (readable by the owner only, written whole), with the phone's
name and when it was paired and last used. A phone that is removed is refused at its next request, and its streams
that are open end at once (:meth:`PairedPhones.removed`). The key is never logged.
"""

import asyncio
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass, replace
from pathlib import Path

from dotagent.core.errors import ConfigError
from dotagent.core.files import write_whole
from dotagent.core.logs import get_logger

log = get_logger(__name__)

KEY_PREFIX: str = "phone."
_ID_BYTES: int = 6
_SECRET_BYTES: int = 32
_KEY_PATTERN: re.Pattern[str] = re.compile(r"phone\.([0-9a-f]{12})\.([A-Za-z0-9_-]{43})")
_ID_PATTERN: re.Pattern[str] = re.compile(r"[0-9a-f]{12}")
# How many phones may hold a key at once; a pairing past them is refused until one is removed.
MAX_PHONES: int = 20
# How seldom a phone's last use is written: every request of it would otherwise write the file.
LAST_USED_EVERY_S: float = 600.0
_NAME_MAX: int = 60


@dataclass(frozen=True)
class Phone:
    """A paired phone as the daemon keeps it: ``key_sha256`` is the hex SHA-256 of its key."""

    id: str
    name: str
    paired_at: float
    last_used_at: float | None
    key_sha256: str


def key_hash(key: str) -> str:
    """The hex SHA-256 of a phone's key: what the daemon keeps of it, and what the proof is keyed with."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def phone_id_of(key: str | None) -> str | None:
    """The id a phone's key names, or ``None`` when ``key`` is not shaped as one."""
    if key is None:
        return None
    found: re.Match[str] | None = _KEY_PATTERN.fullmatch(key)
    return None if found is None else found.group(1)


def is_phone_id(text: str) -> bool:
    return _ID_PATTERN.fullmatch(text) is not None


def phone_name(user_agent: str | None) -> str:
    """What the list calls a phone, from the User-Agent its pairing came with."""
    agent: str = user_agent or ""
    if "iPad" in agent:
        return "iPad"
    if "iPhone" in agent:
        return "iPhone"
    if "Android" in agent:
        return "Android phone"
    return "Phone"


class PairedPhones:
    """The phones that hold a key, read from ``path`` once and written whole on every change. One per daemon: the guard
    and the routes share it."""

    def __init__(self, path: Path, phones: dict[str, Phone]) -> None:
        self._path: Path = path
        self._phones: dict[str, Phone] = phones
        # Set for a phone once it is removed: the guard ends that phone's open streams on it.
        self._removed: dict[str, asyncio.Event] = {}
        self._lock: asyncio.Lock = asyncio.Lock()

    @classmethod
    def load(cls, path: Path) -> "PairedPhones":
        """The phones ``path`` lists; none when it does not exist. A file that cannot be read is refused rather than
        taken as no phones, so that a fault never passes for a phone's removal or a key for another's."""
        try:
            text: str = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return cls(path, {})
        try:
            rows: object = json.loads(text)
            if not isinstance(rows, list):
                raise TypeError("not a list")
            phones: dict[str, Phone] = {}
            for row in rows:
                phone = Phone(
                    id=str(row["id"]),
                    name=str(row["name"])[:_NAME_MAX],
                    paired_at=float(row["paired_at"]),
                    last_used_at=None if row.get("last_used_at") is None else float(row["last_used_at"]),
                    key_sha256=str(row["key_sha256"]),
                )
                if not is_phone_id(phone.id) or re.fullmatch(r"[0-9a-f]{64}", phone.key_sha256) is None:
                    raise ValueError("a phone's id or key hash is not shaped as one")
                phones[phone.id] = phone
        except (ValueError, TypeError, KeyError) as error:
            raise ConfigError(
                f"{path} is not a list of paired phones: fix it or delete it, which unpairs every phone",
                {"path": str(path), "error": str(error)},
            ) from error
        return cls(path, phones)

    def listed(self) -> list[Phone]:
        """The phones, the one paired first first."""
        return sorted(self._phones.values(), key=lambda phone: phone.paired_at)

    def authenticate(self, key: str | None) -> str | None:
        """The id of the listed phone whose key ``key`` is, else ``None``; the hashes are compared in constant time."""
        phone_id: str | None = phone_id_of(key)
        if phone_id is None or key is None:
            return None
        phone: Phone | None = self._phones.get(phone_id)
        if phone is None or not hmac.compare_digest(phone.key_sha256, key_hash(key)):
            return None
        return phone_id

    def proof_key(self, phone_id: str) -> str | None:
        """What the daemon keys its proof with for a phone (item 670): the hash of its key, the one thing it holds."""
        phone: Phone | None = self._phones.get(phone_id)
        return None if phone is None else phone.key_sha256

    def removed(self, phone_id: str) -> asyncio.Event:
        """Set once the phone is removed (at once when it is not listed now): its open streams end on it."""
        event: asyncio.Event = self._removed.setdefault(phone_id, asyncio.Event())
        if phone_id not in self._phones:
            event.set()
        return event

    def check_room(self) -> None:
        """Raise when ``MAX_PHONES`` hold a key already: asked before a code is used up, so that a refused pairing
        leaves its code good."""
        if len(self._phones) >= MAX_PHONES:
            raise ConfigError(
                f"{MAX_PHONES} phones are paired already: remove one in Settings › Phone on your Mac first",
                {"phones": len(self._phones)},
            )

    async def pair(self, user_agent: str | None, now: float) -> tuple[Phone, str]:
        """A new phone and its key; raises when ``MAX_PHONES`` hold one already."""
        async with self._lock:
            self.check_room()
            phone_id: str = secrets.token_hex(_ID_BYTES)
            while phone_id in self._phones:
                phone_id = secrets.token_hex(_ID_BYTES)
            key: str = f"{KEY_PREFIX}{phone_id}.{secrets.token_urlsafe(_SECRET_BYTES)}"
            phone = Phone(phone_id, phone_name(user_agent), now, None, key_hash(key))
            await self._write({**self._phones, phone_id: phone})
            log.info("phone_key_issued", phone=phone_id)
            return phone, key

    async def remove(self, phone_id: str) -> bool:
        """Remove a phone: its key is refused from now on and its open streams end. ``False`` when it is not listed."""
        async with self._lock:
            if phone_id not in self._phones:
                return False
            await self._write({key: phone for key, phone in self._phones.items() if key != phone_id})
            self.removed(phone_id).set()
            log.info("phone_key_removed", phone=phone_id)
            return True

    async def used(self, phone_id: str, now: float) -> None:
        """Note that a phone was used now, written at most every ``LAST_USED_EVERY_S``. A write that fails is logged and
        the request goes on: when a phone was last used never keeps it out."""
        phone: Phone | None = self._phones.get(phone_id)
        if phone is None or (phone.last_used_at is not None and now - phone.last_used_at < LAST_USED_EVERY_S):
            return
        async with self._lock:
            phone = self._phones.get(phone_id)
            if phone is None:
                return
            try:
                await self._write({**self._phones, phone_id: replace(phone, last_used_at=now)})
            except OSError as error:
                log.warning("phone_last_use_not_written", phone=phone_id, error=str(error))

    async def _write(self, phones: dict[str, Phone]) -> None:
        """Write ``phones`` whole, then take them up: a write that fails leaves the file and the list as they were."""
        rows: list[dict[str, object]] = [
            {
                "id": phone.id,
                "name": phone.name,
                "paired_at": phone.paired_at,
                "last_used_at": phone.last_used_at,
                "key_sha256": phone.key_sha256,
            }
            for phone in sorted(phones.values(), key=lambda phone: phone.paired_at)
        ]
        await asyncio.to_thread(write_whole, self._path, json.dumps(rows, indent=2) + "\n")
        self._phones = phones
