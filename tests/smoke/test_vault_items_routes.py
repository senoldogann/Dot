"""Dot's own vault's routes (item 621), through a real in-process server and a stand-in for the Keychain: what a
``GET`` answers holds no secret, a ``POST`` and a ``PUT`` take the values and keep them in the Keychain and not in the
database, a ``DELETE`` removes both, the eye answers one part to this Mac's own address alone, an error names what
failed and never a value, an import counts what it did, and a card's photo is read on this Mac."""

import asyncio
import base64
import dataclasses
import io
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from PIL import Image, ImageDraw, ImageFont
from vault_stand_ins import MemorySecretStore

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.types import JsonObject
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.vault import shown
from dotagent.vault.items import VaultItems

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "vault-items-token"
PHONE: str = "dot-phone.ts.net"
PASSWORD: str = "S3cret-Passw0rd!"
NOTES: str = "recovery code 123456-ZXCV"
NUMBER: str = "4242 4242 4242 4242"
CODE: str = "737"
CHROME_EXPORT: str = (
    "name,url,username,password,note\n"
    "GitHub,https://github.com/login,ada,imported-secret-1,\n"
    "Bad,android://abc@com.example,ada,imported-secret-2,\n"
    "Example,https://example.com,bob,imported-secret-3,a note\n"
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def parts(
    tmp_path_factory: pytest.TempPathFactory,
) -> AsyncIterator[tuple[httpx.AsyncClient, MemorySecretStore, Path]]:
    """A client of the API, on this Mac's own address and on a name a phone reaches it by, over a runtime whose vault
    keeps its secrets in a dictionary; and the folder of the database."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[server]\nreach = ["{PHONE}"]\n', encoding="utf-8"
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    keychain: MemorySecretStore = MemorySecretStore()
    runtime: Runtime = dataclasses.replace(built, vault_items=VaultItems(built.store, keychain))
    manager: RunManager = RunManager(runtime)
    context: ServerContext = ServerContext(
        runtime=runtime,
        manager=manager,
        scheduler=None,
        token=TOKEN,
        host="127.0.0.1",
        port=8765,
        started_at=time.time(),
        stopping=asyncio.Event(),
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(context)),
            base_url="http://127.0.0.1:8765",
            headers={"Authorization": f"Bearer {TOKEN}"},
        ) as http:
            yield http, keychain, home
    finally:
        await manager.shutdown()
        await close_runtime(built)
        patch.undo()


def _everything_on_disk(home: Path) -> bytes:
    """The database and the files beside it, as they are on disk."""
    return b"".join(path.read_bytes() for path in sorted(home.rglob("*.sqlite*")) if path.is_file())


def _words(answer: object) -> list[str]:
    """Every string an answer says, but the ids: they and the times are random digits, in which a code of three digits
    stands by chance now and then (an id is made of letters and digits)."""
    if isinstance(answer, str):
        return [answer]
    if isinstance(answer, dict):
        return [text for key, one in answer.items() if key != "id" for text in _words(one)]
    if isinstance(answer, list):
        return [text for one in answer for text in _words(one)]
    return []


async def _remove_all(client: httpx.AsyncClient) -> None:
    listed: JsonObject = (await client.get("/api/vault/items")).json()
    for item in listed["items"]:  # type: ignore[attr-defined]
        (await client.delete(f"/api/vault/items/{item['id']}")).raise_for_status()


async def test_a_get_answers_no_secret_and_a_post_and_a_put_keep_the_values_in_the_keychain_alone(
    parts: tuple[httpx.AsyncClient, MemorySecretStore, Path],
) -> None:
    client, keychain, home = parts
    login = await client.post(
        "/api/vault/items",
        json={"kind": "login", "website": "github.com", "username": "ada", "password": PASSWORD, "notes": NOTES},
    )
    card = await client.post(
        "/api/vault/items",
        json={
            "kind": "card",
            "number": NUMBER,
            "expiry": "08/29",
            "code": CODE,
            "name": "Ada Lovelace",
            "nickname": "Travel",
            "billing": {"address": "12 Main St", "city": "Izmir"},
        },
    )
    contact = await client.post(
        "/api/vault/items",
        json={"kind": "contact", "label": "Home", "fields": {"name": "Ada Lovelace", "email": "ada@example.com"}},
    )
    assert (login.status_code, card.status_code, contact.status_code) == (200, 200, 200), (login.text, card.text)
    assert card.json()["label"] == "Travel · Visa •••• 4242" and card.json()["last4"] == "4242"
    ids: dict[str, str] = {"login": login.json()["id"], "card": card.json()["id"], "contact": contact.json()["id"]}
    assert keychain.kept[ids["login"]] == {"password": PASSWORD, "notes": NOTES}
    assert keychain.kept[ids["card"]] == {
        "number": "4242424242424242",
        "expiry": "08/29",
        "code": CODE,
        "name": "Ada Lovelace",
    }
    assert ids["contact"] not in keychain.kept

    listed = await client.get("/api/vault/items")
    one = await client.get(f"/api/vault/items/{ids['card']}")
    counts = await client.get("/api/vault/counts")
    assert listed.json()["counts"] == {"login": 1, "card": 1, "contact": 1} == counts.json()
    assert {item["kind"] for item in listed.json()["items"]} == {"login", "card", "contact"}
    # No answer holds a secret: not the list, not one item, not what a post answered.
    for answer in (listed, one, login, card, contact):
        for secret in (PASSWORD, NOTES, "4242424242424242", NUMBER, "08/29"):
            assert secret not in answer.text, (secret, answer.text)
        # The code is three digits, which the times of the answer hold by chance now and then: look where it would
        # be said.
        assert not any(CODE in text for text in _words(answer.json())), answer.text
    # Nor does the database, nor the files beside it.
    disk: bytes = await asyncio.to_thread(_everything_on_disk, home)
    for secret in (PASSWORD, NOTES, "4242424242424242", NUMBER, "08/29"):
        assert secret.encode() not in disk, secret
    assert b"github.com" in disk and b"Travel" in disk

    # A put keeps what it leaves out, in the record and in the Keychain.
    changed = await client.put(
        f"/api/vault/items/{ids['login']}", json={"kind": "login", "username": "ada@work.example"}
    )
    assert changed.status_code == 200 and changed.json()["username"] == "ada@work.example"
    assert changed.json()["website"] == "github.com"
    assert keychain.kept[ids["login"]] == {"password": PASSWORD, "notes": NOTES}
    await _remove_all(client)


async def test_a_delete_removes_the_keychain_item_and_the_record_and_a_second_delete_finds_none(
    parts: tuple[httpx.AsyncClient, MemorySecretStore, Path],
) -> None:
    client, keychain, _ = parts
    made = await client.post("/api/vault/items", json={"kind": "login", "website": "example.com", "password": PASSWORD})
    item_id: str = made.json()["id"]
    assert item_id in keychain.kept
    gone = await client.delete(f"/api/vault/items/{item_id}")
    assert gone.status_code == 200 and gone.json() == {"id": item_id}
    assert item_id not in keychain.kept
    assert (await client.get(f"/api/vault/items/{item_id}")).status_code == 404
    again = await client.delete(f"/api/vault/items/{item_id}")
    assert again.status_code == 404 and "no such item" in again.text


async def test_a_removal_of_several_takes_their_keychain_items_and_records_and_passes_over_an_unknown_id(
    parts: tuple[httpx.AsyncClient, MemorySecretStore, Path],
) -> None:
    """The Delete of the ticked rows (item 768); a list with no id is refused."""
    client, keychain, _ = parts
    made: list[str] = []
    for site in ("one.example", "two.example", "three.example"):
        answer = await client.post("/api/vault/items", json={"kind": "login", "website": site, "password": PASSWORD})
        made.append(answer.json()["id"])
    gone = await client.post("/api/vault/items/remove", json={"ids": [*made[:2], "vlt_never_was"]})
    assert gone.status_code == 200 and gone.json() == {"removed": 2, "stopped": None}
    assert made[0] not in keychain.kept and made[1] not in keychain.kept and made[2] in keychain.kept
    listed: list[str] = [item["id"] for item in (await client.get("/api/vault/items")).json()["items"]]
    assert made[2] in listed and made[0] not in listed and made[1] not in listed
    assert (await client.post("/api/vault/items/remove", json={"ids": []})).status_code == 422
    await client.delete(f"/api/vault/items/{made[2]}")


async def test_the_eye_answers_one_part_to_this_macs_own_address_alone_and_the_list_to_a_phone(
    parts: tuple[httpx.AsyncClient, MemorySecretStore, Path],
) -> None:
    client, _, _ = parts
    made = await client.post(
        "/api/vault/items",
        json={"kind": "login", "website": "example.com", "password": PASSWORD, "notes": NOTES},
    )
    item_id: str = made.json()["id"]
    shown = await client.post(f"/api/vault/items/{item_id}/reveal", json={"part": "password"})
    assert shown.status_code == 200 and shown.json() == {"value": PASSWORD}
    # What it answers is never kept by a browser or a proxy on the way.
    assert shown.headers["cache-control"] == "no-store"
    assert (await client.post(f"/api/vault/items/{item_id}/reveal", json={"part": "notes"})).json() == {"value": NOTES}
    # From a name a phone reaches Dot by, the list answers and the eye does not.
    phone = {"Host": PHONE}
    assert (await client.get("/api/vault/items", headers=phone)).status_code == 200
    refused = await client.post(f"/api/vault/items/{item_id}/reveal", json={"part": "password"}, headers=phone)
    assert refused.status_code == 403 and "Mac itself" in refused.text and PASSWORD not in refused.text
    # A part an item does not keep is refused in words.
    wrong = await client.post(f"/api/vault/items/{item_id}/reveal", json={"part": "number"})
    assert wrong.status_code == 422 and "keeps no number" in wrong.text
    assert (await client.post("/api/vault/items/vlt_nope/reveal", json={"part": "password"})).status_code == 404
    await _remove_all(client)


async def test_the_eye_notes_that_a_secret_may_be_on_the_screen_and_a_refusal_does_not(
    parts: tuple[httpx.AsyncClient, MemorySecretStore, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A screenshot of the Mac leaves out Dot's own windows while a secret may be on the screen: the daemon notes the
    moment it answers the eye, and answers a request that is refused, or asks for a part that is not there, with no
    such note."""
    client, _, _ = parts
    monkeypatch.setattr(shown, "_until", 0.0)
    made = await client.post("/api/vault/items", json={"kind": "login", "website": "example.com", "password": PASSWORD})
    item_id: str = made.json()["id"]
    assert not shown.on_screen()
    refused = await client.post(
        f"/api/vault/items/{item_id}/reveal", json={"part": "password"}, headers={"Host": PHONE}
    )
    wrong = await client.post(f"/api/vault/items/{item_id}/reveal", json={"part": "number"})
    missing = await client.post("/api/vault/items/vlt_nope/reveal", json={"part": "password"})
    assert (refused.status_code, wrong.status_code, missing.status_code) == (403, 422, 404)
    assert not shown.on_screen()
    assert (await client.post(f"/api/vault/items/{item_id}/reveal", json={"part": "password"})).status_code == 200
    assert shown.on_screen()
    await _remove_all(client)


async def test_a_website_that_cannot_be_read_or_has_a_password_in_it_is_refused_in_words_and_never_quoted(
    parts: tuple[httpx.AsyncClient, MemorySecretStore, Path],
) -> None:
    """Python cannot read some addresses and says so by quoting them, a password with them: neither the answer nor the
    vault holds one. An address with a user name or a password in it is no site of a login."""
    client, keychain, _ = parts
    secret: str = "hunter2-Zq9-SecretPw"
    sites: list[str] = [
        f"https://ada:{secret}@host\N{CARE OF}.com/",
        "http://[::1",
        f"https://admin:{secret}@192.168.1.1/",
        f"router:{secret}@192.168.1.1",
    ]
    for site in sites:
        refused = await client.post("/api/vault/items", json={"kind": "login", "website": site, "password": PASSWORD})
        assert refused.status_code == 422, (site, refused.text)
        assert secret not in refused.text and "ValueError" not in refused.text and PASSWORD not in refused.text
    made = await client.post("/api/vault/items", json={"kind": "login", "website": "example.com", "password": PASSWORD})
    for site in sites:
        changed = await client.put(f"/api/vault/items/{made.json()['id']}", json={"kind": "login", "website": site})
        assert changed.status_code == 422 and secret not in changed.text and "ValueError" not in changed.text
    assert (await client.get(f"/api/vault/items/{made.json()['id']}")).json()["website"] == "example.com"
    assert len(keychain.kept) == 1
    # A file that holds such rows is read to its end, and says how many were left out and why.
    rows: str = "".join(f"s{number},{site},ada,pw-{number}\n" for number, site in enumerate(sites))
    imported = await client.post(
        "/api/vault/items/import",
        json={"csv": "name,url,username,password\n" + rows + "ok,https://ok.example,bob,pw-9\n"},
    )
    assert imported.status_code == 200 and secret not in imported.text, imported.text
    answer = imported.json()
    assert answer["imported"] == 1 and {skip["reason"]: skip["count"] for skip in answer["skipped"]} == {
        "not a web address": 2,
        "an address with a user name or a password in it": 2,
    }
    assert secret not in (await client.get("/api/vault/items")).text
    await _remove_all(client)


async def test_an_error_says_what_failed_and_never_a_value(
    parts: tuple[httpx.AsyncClient, MemorySecretStore, Path],
) -> None:
    client, keychain, _ = parts
    bad_number = await client.post(
        "/api/vault/items", json={"kind": "card", "number": "4242 4242 4242 4241", "expiry": "08/29"}
    )
    assert bad_number.status_code == 422 and "does not look right" in bad_number.text
    assert "4242" not in bad_number.text
    expired = await client.post("/api/vault/items", json={"kind": "card", "number": NUMBER, "expiry": "01/20"})
    assert expired.status_code == 422 and "expired" in expired.text and "4242" not in expired.text
    # The framework's own complaint about a field names the field and not its value.
    too_long = await client.post(
        "/api/vault/items", json={"kind": "login", "website": "example.com", "password": "p" * 700}
    )
    assert too_long.status_code == 422 and "password" in too_long.text and "p" * 20 not in too_long.text
    no_kind = await client.post("/api/vault/items", json={"website": "example.com", "password": PASSWORD})
    assert no_kind.status_code == 422 and PASSWORD not in no_kind.text
    wrong_kind = await client.put(
        "/api/vault/items/vlt_nope", json={"kind": "login", "website": "example.com", "password": PASSWORD}
    )
    assert wrong_kind.status_code == 404 and PASSWORD not in wrong_kind.text
    assert keychain.kept == {}


async def test_an_import_counts_what_it_did_and_a_file_that_is_no_list_is_refused_without_quoting_it(
    parts: tuple[httpx.AsyncClient, MemorySecretStore, Path],
) -> None:
    client, keychain, home = parts
    done = await client.post("/api/vault/items/import", json={"csv": CHROME_EXPORT})
    assert done.status_code == 200, done.text
    report: JsonObject = done.json()
    assert (report["format"], report["rows"], report["imported"], report["stopped"]) == ("Chrome or Edge", 3, 2, None)
    assert report["skipped"] == [{"reason": "not a web address", "count": 1}]
    assert "imported-secret" not in done.text
    listed = await client.get("/api/vault/items")
    assert sorted(item["username"] for item in listed.json()["items"]) == ["ada", "bob"]
    assert "imported-secret" not in listed.text
    assert sorted(parts_["password"] for parts_ in keychain.kept.values()) == ["imported-secret-1", "imported-secret-3"]
    disk: bytes = await asyncio.to_thread(_everything_on_disk, home)
    assert b"imported-secret" not in disk
    refused = await client.post("/api/vault/items/import", json={"csv": "just,some\nwords,here\n"})
    assert refused.status_code == 422 and "not a list of logins" in refused.text and "words" not in refused.text
    await _remove_all(client)


def _card_picture() -> str:
    """A card as a picture, in base64: its number, its expiry and its name in capitals."""
    image: Image.Image = Image.new("RGB", (860, 540), (20, 40, 90))
    draw: ImageDraw.ImageDraw = ImageDraw.Draw(image)
    big = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 54)
    small = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 32)
    draw.text((50, 90), "VISA", fill=(255, 255, 255), font=big)
    draw.text((50, 250), NUMBER, fill=(255, 255, 255), font=big)
    draw.text((50, 360), "VALID THRU 12/39", fill=(255, 255, 255), font=small)
    draw.text((50, 440), "ADA LOVELACE", fill=(255, 255, 255), font=small)
    buffer: io.BytesIO = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


async def test_a_cards_photo_is_read_on_this_mac_and_gives_the_dialog_what_it_found(
    parts: tuple[httpx.AsyncClient, MemorySecretStore, Path],
) -> None:
    client, keychain, _ = parts
    read = await client.post("/api/vault/card-photo", json={"image": _card_picture()})
    assert read.status_code == 200, read.text
    assert read.json() == {"number": "4242424242424242", "expiry": "12/39", "name": "ADA LOVELACE"}
    # The photo is read and dropped: nothing was kept.
    assert keychain.kept == {} and (await client.get("/api/vault/items")).json()["items"] == []
    blank = Image.new("RGB", (200, 100), (255, 255, 255))
    buffer: io.BytesIO = io.BytesIO()
    blank.save(buffer, format="PNG")
    nothing = await client.post("/api/vault/card-photo", json={"image": base64.b64encode(buffer.getvalue()).decode()})
    assert nothing.json() == {"number": None, "expiry": None, "name": None}
    not_a_picture = await client.post(
        "/api/vault/card-photo", json={"image": base64.b64encode(b"not a picture").decode()}
    )
    assert not_a_picture.status_code == 422, not_a_picture.text
    assert (await client.post("/api/vault/card-photo", json={"image": "***"})).status_code == 422
