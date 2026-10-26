"""Routes of Dot's own vault (item 621), Settings › Vault: the list of the logins, cards and contacts the owner saved,
one item, and saving, changing and removing them; the eye, which shows one secret part to this Mac's own page; an
import of the logins of a CSV export; and a card's photo, read on this Mac.

What a ``GET`` answers holds no secret: the database's records, which never do. ``POST`` and ``PUT`` take the values
(the models of the requests hold them as ``SecretStr``, so that no repr, log line or error shows one), ``DELETE``
removes the Keychain item as well as the record. The eye is ``POST …/reveal``, answered to a request that names this
Mac's own address alone (as 1Password's connect is, :func:`dotagent.server.routes_accounts.on_this_mac`) and never
kept. What the API says of a failure names what failed and never a value; the text of an import and the picture of a
card are read in memory and dropped."""

import base64
import binascii
import time
from datetime import date

from fastapi import APIRouter, Request
from pydantic import Field, SecretStr

from dotagent.core.errors import InvalidValueError, VaultRefused
from dotagent.core.types import FrozenModel
from dotagent.server.context import ServerContext
from dotagent.server.routes_accounts import on_this_mac
from dotagent.store.models import VaultRecord
from dotagent.vault.items import ITEM_DRAFT, ImportReport
from dotagent.vault.ocr import MOST_BYTES, CardPhoto, read_card_photo
from dotagent.vault.shown import note_shown

# The most characters of a CSV export one import takes, and of a card's photo in base64.
_MOST_CSV_CHARS: int = 8_000_000
_MOST_PHOTO_CHARS: int = MOST_BYTES * 4 // 3 + 16


class VaultList(FrozenModel):
    """Every item of the vault, and how many there are of each kind."""

    items: list[VaultRecord]
    counts: dict[str, int]


class Removed(FrozenModel):
    id: str


class RevealRequest(FrozenModel):
    """The secret part the owner pressed the eye of: a login's ``password`` or ``notes``, a card's ``number``,
    ``expiry``, ``code`` or ``name``."""

    part: str = Field(min_length=1, max_length=20)


class Revealed(FrozenModel):
    """The one part asked for, to this Mac's page, once."""

    value: str


class ImportRequest(FrozenModel):
    """The text of a CSV export: it holds passwords in plain text, so it is a ``SecretStr`` and is read in memory."""

    csv: SecretStr = Field(max_length=_MOST_CSV_CHARS)


class PhotoRequest(FrozenModel):
    """A photo of a card, in base64."""

    image: SecretStr = Field(min_length=1, max_length=_MOST_PHOTO_CHARS)


def vault_items_router(ctx: ServerContext) -> APIRouter:
    router: APIRouter = APIRouter(prefix="/api/vault")

    def on_the_mac(request: Request) -> None:
        if not on_this_mac(request):
            raise VaultRefused(
                "show it on the Mac itself: a secret of the vault is shown to this Mac's own page alone", {}
            )

    async def listing() -> VaultList:
        return VaultList(items=await ctx.runtime.vault_items.items(), counts=await ctx.runtime.vault_items.counts())

    @router.get("/counts")
    async def counts() -> dict[str, int]:
        """How many items of each kind the vault holds: what the settings' list of tabs says beside Vault."""
        return await ctx.runtime.vault_items.counts()

    @router.get("/items")
    async def items() -> VaultList:
        """The vault's items by label, with their counts: no secret among them."""
        return await listing()

    @router.post("/items")
    async def create(body: ITEM_DRAFT) -> VaultRecord:
        """Save a login, a card or a contact: its secrets in the Keychain, its record in the database."""
        return await ctx.runtime.vault_items.create(body)

    @router.post("/items/import")
    async def import_logins(body: ImportRequest) -> ImportReport:
        """Save the logins of a CSV export of a password manager. The text is read in memory and dropped."""
        return await ctx.runtime.vault_items.import_logins(body.csv.get_secret_value())

    @router.get("/items/{item_id}")
    async def item(item_id: str) -> VaultRecord:
        return await ctx.runtime.vault_items.item(item_id)

    @router.put("/items/{item_id}")
    async def change(item_id: str, body: ITEM_DRAFT) -> VaultRecord:
        """Change an item: what the request leaves out stays as it is."""
        return await ctx.runtime.vault_items.update(item_id, body)

    @router.delete("/items/{item_id}")
    async def remove(item_id: str) -> Removed:
        """Remove an item: its Keychain item and its record."""
        await ctx.runtime.vault_items.remove(item_id)
        return Removed(id=item_id)

    @router.post("/items/{item_id}/reveal")
    async def reveal(item_id: str, body: RevealRequest, request: Request) -> Revealed:
        """Show one secret part of an item to the owner who pressed the eye: to this Mac's own page alone."""
        on_the_mac(request)
        value: str = (await ctx.runtime.vault_items.reveal(item_id, body.part)).get_secret_value()
        # A secret may be on the screen from here: a screenshot of the Mac leaves out Dot's own windows for a while.
        note_shown()
        return Revealed(value=value)

    @router.post("/card-photo")
    async def card_photo(body: PhotoRequest) -> CardPhoto:
        """The number, expiry and name that Vision reads in a card's photo, on this Mac: for the dialog to fill in and
        the owner to check."""
        try:
            image: bytes = base64.b64decode(body.image.get_secret_value(), validate=True)
        except (binascii.Error, ValueError) as error:
            raise InvalidValueError("the picture could not be read: send a photo of the card", {}) from error
        return await read_card_photo(image, date.fromtimestamp(time.time()))

    return router
