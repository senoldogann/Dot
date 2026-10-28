"""Settings › Vault in a real browser (item 621), at 1280 and at 390 pixels: the empty page and what Dot never sees;
the New item menu; the three forms, each off until it is good, saying under a field what is wrong as it is typed and
inside the dialog what the daemon refused, with what was typed kept; the tabs with their counts and the search; Edit,
with a secret shown only when its eye is pressed, and Delete; an import of a made CSV and what the page says it did; and
a card's photo read on this Mac. The Keychain is a stand-in: no item of the owner's is made, read or removed."""

import asyncio
import dataclasses
import io
import json
import re
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from form_dialogs import (
    asks_before_dropping,
    foot_is_in_view,
    keeps_inside_the_window,
    open_form,
    shows_the_failure,
)
from PIL import Image, ImageDraw, ImageFont
from playwright.async_api import Browser, Locator, Page, Route, async_playwright, expect
from test_plain_words import JARGON, RUN_AS_A_THING, VISIBLE
from vault_stand_ins import MemorySecretStore

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.vault import shown
from dotagent.vault.items import CardDraft, ContactDraft, LoginDraft, VaultItems

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "vault-page-token"
PASSWORD: str = "S3cret-Passw0rd!"
NUMBER: str = "4242 4242 4242 4242"
CSV: bytes = (
    b"name,url,username,password,note\n"
    b"GitHub,https://github.com/login,ada,imported-secret-1,\n"
    b"Bad,android://abc@com.example,ada,imported-secret-2,\n"
    b"Example,https://example.com,bob,imported-secret-3,\n"
)


@pytest_asyncio.fixture(loop_scope="module")
async def parts(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[Runtime, MemorySecretStore]]:
    """A runtime in a home of its own whose vault keeps its secrets in a dictionary."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[memory]\nembed_enabled = false\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    keychain: MemorySecretStore = MemorySecretStore()
    try:
        yield dataclasses.replace(built, vault_items=VaultItems(built.store, keychain)), keychain
    finally:
        await close_runtime(built)


@pytest_asyncio.fixture(loop_scope="module")
async def address(parts: tuple[Runtime, MemorySecretStore]) -> AsyncIterator[str]:
    runtime, _ = parts
    manager: RunManager = RunManager(runtime)
    stopping: asyncio.Event = asyncio.Event()
    listener = bind_listener("127.0.0.1", 0)
    port: int = listener.getsockname()[1]
    context: ServerContext = ServerContext(
        runtime=runtime,
        manager=manager,
        scheduler=None,
        token=TOKEN,
        host="127.0.0.1",
        port=port,
        started_at=time.time(),
        stopping=stopping,
    )
    server: DotServer = DotServer(serving_config(create_app(context)), stopping)
    serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
    while not server.started:
        assert not serving.done(), "the server stopped while starting"
        await asyncio.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()


@pytest_asyncio.fixture(loop_scope="module")
async def browser(parts: tuple[Runtime, MemorySecretStore]) -> AsyncIterator[Browser]:
    async with async_playwright() as playwright:
        launched: Browser = await playwright.chromium.launch(channel=parts[0].config.browser.channel, headless=True)
        try:
            yield launched
        finally:
            await launched.close()


@pytest_asyncio.fixture(loop_scope="module", autouse=True)
async def empty(parts: tuple[Runtime, MemorySecretStore]) -> AsyncIterator[None]:
    """Each test starts from an empty vault."""
    runtime, keychain = parts
    for record in await runtime.vault_items.items():
        await runtime.vault_items.remove(record.id)
    keychain.kept.clear()
    yield


async def _open(browser: Browser, address: str, width: int = 1280, height: int = 900) -> Page:
    # Under 720 pixels the page is a phone's: a touch screen, which draws its text fields at 16 px.
    phone: bool = width < 720
    page: Page = await browser.new_page(
        viewport={"width": width, "height": height},
        reduced_motion="reduce",
        is_mobile=phone,
        has_touch=phone,
        device_scale_factor=3 if phone else 1,
    )
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url("127.0.0.1", int(address.rpartition(":")[2]), TOKEN))
    await page.goto(f"{address}/#/settings/vault")
    await expect(page.get_by_role("heading", name="Vault", level=1)).to_be_visible()
    return page


async def _seed(runtime: Runtime) -> None:
    """Two logins, two cards (one with a billing address) and two contacts."""
    vault: VaultItems = runtime.vault_items
    await vault.create(LoginDraft(kind="login", website="github.com", username="ada@example.com", password=PASSWORD))  # type: ignore[arg-type]
    await vault.create(LoginDraft(kind="login", website="https://shop.example.org", password=PASSWORD))  # type: ignore[arg-type]
    await vault.create(
        CardDraft(kind="card", number=NUMBER, expiry="08/39", code="737", nickname="Travel", billing={"city": "Izmir"})  # type: ignore[arg-type]
    )
    await vault.create(CardDraft(kind="card", number="3782 822463 10005", expiry="09/39"))  # type: ignore[arg-type]
    await vault.create(ContactDraft(kind="contact", label="Home", fields={"name": "Ada Lovelace", "city": "Izmir"}))
    await vault.create(ContactDraft(kind="contact", label="Work", fields={"email": "ada@work.example"}))


async def _tree(page: Page) -> str:
    """Everything Chrome's accessibility tree gives of the page: the names, the descriptions and the values of its
    boxes, which anything that reads the Mac's windows reads (a box drawn as dots by a style is read as it is)."""
    session = await page.context.new_cdp_session(page)
    try:
        return json.dumps(await session.send("Accessibility.getFullAXTree"))
    finally:
        await session.detach()


def _tab(page: Page, name: str) -> Locator:
    return page.get_by_role("group", name="Show").get_by_role("button", name=name)


async def _new_item(page: Page, kind: str) -> Locator:
    """Open the form of a kind from the New item menu; returns its dialog."""
    button: Locator = page.get_by_role("button", name="New item")
    await button.click()
    menu: Locator = page.get_by_role("menu", name="New item")
    await expect(menu).to_be_visible()
    return await open_form(page, menu.get_by_role("menuitem", name=kind, exact=True), f"New {kind.lower()}")


async def test_an_empty_vault_says_so_and_what_dot_never_sees(browser: Browser, address: str) -> None:
    page: Page = await _open(browser, address)
    try:
        await expect(page.get_by_text("Nothing saved yet")).to_be_visible()
        await expect(page.get_by_text("Add a login, a card or contact details with New item.")).to_be_visible()
        await expect(
            page.get_by_text("never sees a password, a saved card's number, its expiry or its security code")
        ).to_be_visible()
        for name in ("All, 0", "Logins, 0", "Cards, 0", "Contacts, 0"):
            await expect(_tab(page, name)).to_be_visible()
        await expect(page.get_by_role("searchbox", name="Search the vault")).to_be_visible()
        # The tab stands in the settings' list, in the group of this Mac.
        await expect(page.get_by_role("link", name="Vault")).to_have_attribute("aria-current", "page")
        button: Locator = page.get_by_role("button", name="New item")
        await button.click()
        menu: Locator = page.get_by_role("menu", name="New item")
        for entry in ("Login", "Card", "Contact", "Import from CSV…"):
            await expect(menu.get_by_role("menuitem", name=entry, exact=True)).to_be_visible()
        await page.keyboard.press("Escape")
        await expect(menu).to_have_count(0)
    finally:
        await page.close()


async def test_a_login_is_off_until_it_is_good_keeps_what_was_typed_when_refused_and_then_appears(
    browser: Browser, address: str, parts: tuple[Runtime, MemorySecretStore]
) -> None:
    _, keychain = parts
    page: Page = await _open(browser, address)
    try:
        dialog: Locator = await _new_item(page, "Login")
        save: Locator = dialog.get_by_role("button", name="Save login")
        website: Locator = dialog.get_by_role("textbox", name="Website")
        password: Locator = dialog.get_by_label("Password", exact=True)
        await expect(dialog.get_by_text("can use this login to sign in for you")).to_be_visible()
        await expect(save).to_be_disabled()
        await website.fill("ftp://files.example")
        await expect(save).to_be_disabled()
        await password.fill(PASSWORD)
        await expect(save).to_be_enabled()
        # The password is dots until the eye is pressed.
        await expect(password).to_have_attribute("type", "password")
        await dialog.get_by_role("button", name="Show the password").click()
        await expect(password).to_have_attribute("type", "text")
        await dialog.get_by_role("button", name="Hide the password").click()
        await expect(password).to_have_attribute("type", "password")
        # The daemon refuses a site that is no web page: the reason is in the dialog, and what was typed is still there.
        await save.click()
        await shows_the_failure(dialog, "Dot fills logins on web pages")
        await expect(website).to_have_value("ftp://files.example")
        await expect(password).to_have_value(PASSWORD)
        await expect(save).to_be_enabled()
        assert keychain.kept == {}
        await website.fill("github.com")
        await dialog.get_by_role("textbox", name="Username or email (optional)").fill("ada@example.com")
        await save.click()
        await expect(dialog).to_have_count(0)
        row: Locator = page.locator("[data-vault-item][data-kind=login]")
        await expect(row).to_have_count(1)
        await expect(row.get_by_text("github.com", exact=True)).to_be_visible()
        await expect(row.get_by_text("ada@example.com")).to_be_visible()
        await expect(_tab(page, "Logins, 1")).to_be_visible()
        await expect(_tab(page, "All, 1")).to_be_visible()
        assert list(keychain.kept.values()) == [{"password": PASSWORD}]
        assert PASSWORD not in await page.content()
        await expect(page.get_by_role("button", name="New item")).to_be_focused()
    finally:
        await page.close()


async def test_a_card_is_checked_as_it_is_typed_and_off_until_it_is_good(
    browser: Browser, address: str, parts: tuple[Runtime, MemorySecretStore]
) -> None:
    _, keychain = parts
    page: Page = await _open(browser, address)
    try:
        dialog: Locator = await _new_item(page, "Card")
        save: Locator = dialog.get_by_role("button", name="Save card")
        number: Locator = dialog.get_by_label("Card number", exact=True)
        expiry: Locator = dialog.get_by_label("Expiry (MM / YY)")
        code: Locator = dialog.get_by_label("Security code (optional)", exact=True)
        await expect(dialog.get_by_text("never sees the number")).to_be_visible()
        await expect(save).to_be_disabled()
        # A number whose check digit does not fit says so; the number is grouped as it is typed.
        await number.press_sequentially("4242424242424241")
        await expect(number).to_have_value("4242 4242 4242 4241")
        await expect(dialog.get_by_text("This number does not look right: check its digits.")).to_be_visible()
        await expect(dialog.locator(".vault-brand")).to_have_text("Visa")
        await expiry.press_sequentially("0829")
        await expect(expiry).to_have_value("08/29")
        await expect(save).to_be_disabled()
        await number.press_sequentially("", delay=0)
        await number.fill("4242424242424242")
        await expect(number).to_have_value("4242 4242 4242 4242")
        await expect(dialog.get_by_text("This number does not look right")).to_have_count(0)
        await expect(save).to_be_enabled()
        # The expiry: a month that is none, a card that has expired.
        await expiry.fill("13/29")
        await expect(dialog.get_by_text("Write the expiry as month and year, like 08/29.")).to_be_visible()
        await expect(save).to_be_disabled()
        await expiry.fill("01/20")
        await expect(dialog.get_by_text("This card has expired.")).to_be_visible()
        await expiry.fill("08/39")
        await expect(save).to_be_enabled()
        # The security code: three or four digits, and shown only when the eye is pressed.
        await expect(code).to_have_attribute("type", "password")
        await code.fill("12")
        await expect(dialog.get_by_text("A security code has 3 or 4 digits.")).to_be_visible()
        await expect(save).to_be_disabled()
        await code.fill("737")
        await dialog.get_by_role("button", name="Show the security code").click()
        await expect(code).to_have_attribute("type", "text")
        await expect(save).to_be_enabled()
        await dialog.get_by_role("textbox", name="Nickname (optional)").fill("Travel")
        await dialog.get_by_role("textbox", name="Name on the card (optional)").fill("Ada Lovelace")
        # A billing address is added when asked for.
        billing: Locator = dialog.get_by_role("button", name="Add a billing address (optional)")
        await billing.click()
        await dialog.get_by_label("City").fill("Izmir")
        await dialog.get_by_label("Country").select_option(label="Germany")
        await save.click()
        await expect(dialog).to_have_count(0)
        row: Locator = page.locator("[data-vault-item][data-kind=card]")
        await expect(row).to_have_count(1)
        await expect(row.get_by_text("Travel", exact=True)).to_be_visible()
        await expect(row.get_by_text("Visa •••• 4242")).to_be_visible()
        await expect(row.get_by_text("With a billing address")).to_be_visible()
        await expect(_tab(page, "Cards, 1")).to_be_visible()
        assert list(keychain.kept.values()) == [
            {"number": "4242424242424242", "expiry": "08/39", "code": "737", "name": "Ada Lovelace"}
        ]
        # The page as it is, but for the id of each row: random letters and digits, in which a code of three digits
        # stands by chance now and then.
        content: str = await page.evaluate(
            "() => { const copy = document.documentElement.cloneNode(true);"
            " copy.querySelectorAll('[data-vault-item]').forEach((row) => row.removeAttribute('data-vault-item'));"
            " return copy.outerHTML; }"
        )
        for secret in ("4242424242424242", NUMBER, "08/39", "737"):
            assert secret not in content, secret
    finally:
        await page.close()


async def test_a_contact_needs_a_label_and_a_detail_and_a_good_email(
    browser: Browser, address: str, parts: tuple[Runtime, MemorySecretStore]
) -> None:
    runtime, keychain = parts
    page: Page = await _open(browser, address)
    try:
        dialog: Locator = await _new_item(page, "Contact")
        save: Locator = dialog.get_by_role("button", name="Save contact")
        await expect(save).to_be_disabled()
        await dialog.get_by_role("textbox", name="Label").fill("Home")
        await expect(save).to_be_disabled()
        email: Locator = dialog.get_by_label("Email")
        await email.fill("not-an-email")
        await expect(dialog.get_by_text("The email address does not look right.")).to_be_visible()
        await expect(save).to_be_disabled()
        await email.fill("ada@example.com")
        await expect(save).to_be_enabled()
        await dialog.get_by_label("Full name").fill("Ada Lovelace")
        await dialog.get_by_label("Address", exact=True).fill("12 Main St")
        await dialog.get_by_label("Country").select_option(label="Germany")
        await save.click()
        await expect(dialog).to_have_count(0)
        row: Locator = page.locator("[data-vault-item][data-kind=contact]")
        await expect(row).to_have_count(1)
        await expect(row.get_by_text("Home", exact=True)).to_be_visible()
        await expect(row.get_by_text("Ada Lovelace · 12 Main St")).to_be_visible()
        await expect(_tab(page, "Contacts, 1")).to_be_visible()
        assert keychain.kept == {}
        (record,) = await runtime.vault_items.of_kind("contact")
        assert record.fields["email"] == "ada@example.com" and record.fields["country"] == "Germany"
    finally:
        await page.close()


async def test_the_tabs_count_and_narrow_the_items_and_the_search_finds_by_any_word(
    browser: Browser, address: str, parts: tuple[Runtime, MemorySecretStore]
) -> None:
    runtime, _ = parts
    await _seed(runtime)
    page: Page = await _open(browser, address)
    try:
        rows: Locator = page.locator("[data-vault-item]")
        await expect(rows).to_have_count(6)
        for name in ("All, 6", "Logins, 2", "Cards, 2", "Contacts, 2"):
            await expect(_tab(page, name)).to_be_visible()
        await _tab(page, "Cards, 2").click()
        await expect(rows).to_have_count(2)
        await expect(page.locator("[data-vault-item][data-kind=card]")).to_have_count(2)
        await _tab(page, "Contacts, 2").click()
        await expect(rows).to_have_count(2)
        await _tab(page, "All, 6").click()
        search: Locator = page.get_by_role("searchbox", name="Search the vault")
        await search.fill("izmir")
        # The contact's city, and the card's billing address.
        await expect(rows).to_have_count(2)
        await search.fill("4242")
        await expect(rows).to_have_count(1)
        await search.fill("shop example")
        await expect(rows).to_have_count(1)
        await expect(rows.get_by_text("shop.example.org", exact=True)).to_be_visible()
        await search.fill("nothing like this")
        await expect(page.get_by_text("Nothing matches")).to_be_visible()
        await search.fill("")
        await expect(rows).to_have_count(6)
        # The search narrows within a tab.
        await _tab(page, "Logins, 2").click()
        await search.fill("github")
        await expect(rows).to_have_count(1)
    finally:
        await page.close()


async def test_edit_shows_a_secret_only_when_its_eye_is_pressed_and_delete_removes_the_item_and_its_keychain_item(
    browser: Browser, address: str, parts: tuple[Runtime, MemorySecretStore]
) -> None:
    runtime, keychain = parts
    await _seed(runtime)
    page: Page = await _open(browser, address)
    try:
        row: Locator = page.locator("[data-vault-item][data-kind=login]", has_text="github.com")
        edit: Locator = row.get_by_role("button", name="Edit github.com")
        dialog: Locator = await open_form(page, edit, "Edit login")
        password: Locator = dialog.get_by_label("Password", exact=True)
        await expect(dialog.get_by_role("textbox", name="Website")).to_have_value("github.com")
        await expect(password).to_have_value("")
        await expect(password).to_have_attribute("placeholder", "Saved: write to change it")
        assert PASSWORD not in await page.content()
        await dialog.get_by_role("button", name="Show the password").click()
        await expect(password).to_have_value(PASSWORD)
        await expect(password).to_have_attribute("type", "text")
        await asks_before_dropping(
            page, dialog, dialog.get_by_role("textbox", name="Username or email (optional)"), "x"
        )
        # Changing only the username keeps the password.
        dialog = await open_form(page, row.get_by_role("button", name="Edit github.com"), "Edit login")
        await dialog.get_by_role("textbox", name="Username or email (optional)").fill("ada@elsewhere.example")
        await dialog.get_by_role("button", name="Save login").click()
        await expect(dialog).to_have_count(0)
        await expect(page.get_by_text("ada@elsewhere.example")).to_be_visible()
        assert PASSWORD in [parts_.get("password") for parts_ in keychain.kept.values()]
        assert PASSWORD not in await page.content()
        # A card: the saved number is hidden; the eye shows it.
        card: Locator = page.locator("[data-vault-item][data-kind=card]", has_text="Travel")
        dialog = await open_form(page, card.get_by_role("button", name="Edit Travel"), "Edit card")
        number: Locator = dialog.get_by_label("Card number", exact=True)
        await expect(number).to_have_attribute("placeholder", "Saved: •••• 4242")
        await expect(dialog.get_by_label("City")).to_have_value("Izmir")
        await dialog.get_by_role("button", name="Show the card number").click()
        await expect(number).to_have_value(NUMBER)
        await dialog.get_by_role("button", name="Cancel").click()
        await expect(dialog).to_have_count(0)
        # Delete asks first; a no leaves the item; a yes removes it and its Keychain item.
        before: int = len(keychain.kept)
        await row.get_by_role("button", name="Delete github.com").click()
        question: Locator = page.get_by_role("dialog", name="Delete this login?")
        await expect(question).to_be_visible()
        await question.get_by_role("button", name="Cancel").click()
        await expect(row).to_have_count(1)
        await row.get_by_role("button", name="Delete github.com").click()
        await question.get_by_role("button", name="Delete", exact=True).click()
        await expect(row).to_have_count(0)
        await expect(_tab(page, "Logins, 1")).to_be_visible()
        assert len(keychain.kept) == before - 1
    finally:
        await page.close()


async def test_select_ticks_rows_a_shift_click_ticks_a_run_and_delete_removes_the_ticked_ones(
    browser: Browser, address: str, parts: tuple[Runtime, MemorySecretStore]
) -> None:
    """Item 768: Select turns the rows into ticks (a row's words tick it too), Select all and Clear, a search that
    hides a ticked row unticks it, and Delete asks first and removes the ticked items and their Keychain items."""
    runtime, keychain = parts
    await _seed(runtime)
    page: Page = await _open(browser, address)
    try:
        rows: Locator = page.locator("[data-vault-item]")
        await expect(rows).to_have_count(6)
        order: list[str] = [await row.get_attribute("data-vault-item") or "" for row in await rows.all()]
        await page.get_by_role("button", name="Select", exact=True).click()
        bar: Locator = page.locator(".vault-select-bar")
        delete: Locator = bar.get_by_role("button", name="Delete", exact=True)
        await expect(bar.get_by_text("Choose items to delete")).to_be_visible()
        await expect(delete).to_be_disabled()
        boxes: Locator = page.locator("input.vault-check")
        await expect(boxes).to_have_count(6)
        await expect(page.get_by_role("checkbox", name="github.com, ada@example.com")).to_be_visible()
        # No Edit or Delete on a row while its tick is shown.
        await expect(page.get_by_role("button", name="Edit github.com")).to_have_count(0)
        await bar.get_by_role("button", name="Select all").click()
        await expect(bar.get_by_text("6 items selected")).to_be_visible()
        await bar.get_by_role("button", name="Clear").click()
        await expect(bar.get_by_text("Choose items to delete")).to_be_visible()
        # A shift-click ticks every row from the one ticked last; a click on a row's words ticks it.
        await boxes.nth(0).click()
        await boxes.nth(2).click(modifiers=["Shift"])
        await expect(bar.get_by_text("3 items selected")).to_be_visible()
        await rows.nth(4).locator(".vault-title").click()
        await expect(bar.get_by_text("4 items selected")).to_be_visible()
        assert [await box.is_checked() for box in await boxes.all()] == [True, True, True, False, True, False]
        # A ticked row a search hides is unticked: Delete removes what is ticked in sight.
        await bar.get_by_role("button", name="Select all").click()
        await expect(bar.get_by_text("6 items selected")).to_be_visible()
        search: Locator = page.get_by_role("searchbox", name="Search the vault")
        await search.fill("github")
        await expect(rows).to_have_count(1)
        await expect(bar.get_by_text("1 item selected")).to_be_visible()
        await search.fill("")
        await expect(rows).to_have_count(6)
        github: int = order.index(await rows.filter(has_text="github.com").get_attribute("data-vault-item") or "")
        others: list[int] = [index for index in range(6) if index != github]
        await boxes.nth(others[0]).click()
        await boxes.nth(others[1]).click()
        await expect(bar.get_by_text("3 items selected")).to_be_visible()
        chosen: set[str] = {order[github], order[others[0]], order[others[1]]}
        await delete.click()
        question: Locator = page.get_by_role("dialog", name="Delete 3 items?")
        await expect(question).to_be_visible()
        await question.get_by_role("button", name="Cancel").click()
        await expect(rows).to_have_count(6)
        await delete.click()
        await question.get_by_role("button", name="Delete", exact=True).click()
        await expect(rows).to_have_count(3)
        await expect(page.get_by_text("Deleted 3 items.")).to_be_visible()
        assert {await row.get_attribute("data-vault-item") for row in await rows.all()} == set(order) - chosen
        assert not chosen & set(keychain.kept)
        # The choosing ends with the delete: the rows have their Edit and Delete again.
        await expect(bar).to_be_hidden()
        await expect(page.get_by_role("button", name="Select", exact=True)).to_be_visible()
        await expect(page.locator(".vault-actions")).to_have_count(3)
    finally:
        await page.close()


async def test_a_long_list_scrolls_in_its_card_and_draws_its_rows_as_it_is_scrolled(
    browser: Browser, address: str, parts: tuple[Runtime, MemorySecretStore]
) -> None:
    """Item 768: no Show more to press; Select all ticks every item listed, the rows not drawn yet too."""
    runtime, keychain = parts
    export: str = "name,url,username,password,note\n" + "".join(
        f"s{n:03},https://site{n:03}.example,user{n},pw-{n},\n" for n in range(150)
    )
    await runtime.vault_items.import_logins(export)
    page: Page = await _open(browser, address)
    try:
        rows: Locator = page.locator("[data-vault-item]")
        await expect(rows).to_have_count(60)
        await expect(page.get_by_role("button", name="Show more")).to_have_count(0)
        scroller: Locator = page.locator(".vault-rows")
        assert await scroller.evaluate("(list) => list.scrollHeight > list.clientHeight + 100")
        await scroller.hover()
        await page.mouse.wheel(0, 100_000)
        await expect(rows).to_have_count(120)
        await scroller.evaluate("(list) => { list.scrollTop = list.scrollHeight; }")
        await expect(rows).to_have_count(150)
        await expect(rows.last).to_contain_text("site149.example")
        # A delete keeps the list where it was scrolled.
        await page.get_by_role("button", name="Select", exact=True).click()
        await page.get_by_role("checkbox", name="site149.example, user149").click()
        await page.locator(".vault-select-bar").get_by_role("button", name="Delete", exact=True).click()
        await page.get_by_role("dialog", name="Delete 1 item?").get_by_role("button", name="Delete", exact=True).click()
        await expect(rows).to_have_count(149)
        assert await scroller.evaluate("(list) => list.scrollTop > 1000")
        await page.reload()
        await expect(rows).to_have_count(60)
        await page.get_by_role("button", name="Select", exact=True).click()
        bar: Locator = page.locator(".vault-select-bar")
        await bar.get_by_role("button", name="Select all").click()
        await expect(bar.get_by_text("149 items selected")).to_be_visible()
        await bar.get_by_role("button", name="Delete", exact=True).click()
        await (
            page.get_by_role("dialog", name="Delete 149 items?")
            .get_by_role("button", name="Delete", exact=True)
            .click()
        )
        await expect(page.get_by_text("Nothing saved yet")).to_be_visible()
        await expect(page.get_by_role("button", name="Select", exact=True)).to_be_hidden()
        assert keychain.kept == {}
    finally:
        await page.close()


async def test_a_saved_name_on_a_card_is_kept_when_left_alone_and_read_by_its_eye_and_removed_by_emptying_it(
    browser: Browser, address: str, parts: tuple[Runtime, MemorySecretStore]
) -> None:
    """Pre-push review, round 5: the name on a card is kept in the Keychain as its code is, and the dialog could change
    it but never take it away. It has an eye now, as the code has: left alone it stays; shown, it is read, changed or
    emptied, and emptied it is removed."""
    runtime, keychain = parts
    made = await runtime.vault_items.create(
        CardDraft(kind="card", number=NUMBER, expiry="08/39", name="Ada Lovelace", nickname="Everyday")  # type: ignore[arg-type]
    )
    page: Page = await _open(browser, address)
    try:
        card: Locator = page.locator("[data-vault-item][data-kind=card]", has_text="Everyday")
        dialog: Locator = await open_form(page, card.get_by_role("button", name="Edit Everyday"), "Edit card")
        name: Locator = dialog.get_by_label("Name on the card (optional)")
        await expect(name).to_have_value("")
        await expect(name).to_have_attribute("placeholder", "Saved: write to change it")
        assert "Ada Lovelace" not in await page.content()
        # Left alone, the name stays: only the nickname changes.
        await dialog.get_by_label("Nickname (optional)").fill("Daily")
        await dialog.get_by_role("button", name="Save card").click()
        await expect(dialog).to_have_count(0)
        assert keychain.kept[made.id]["name"] == "Ada Lovelace"
        # The eye reads it; emptied, it goes, and the rest of the card stays.
        card = page.locator("[data-vault-item][data-kind=card]", has_text="Daily")
        dialog = await open_form(page, card.get_by_role("button", name="Edit Daily"), "Edit card")
        name = dialog.get_by_label("Name on the card (optional)")
        await dialog.get_by_role("button", name="Show the name on the card").click()
        await expect(name).to_have_value("Ada Lovelace")
        await name.fill("")
        await dialog.get_by_role("button", name="Save card").click()
        await expect(dialog).to_have_count(0)
        assert "name" not in keychain.kept[made.id] and keychain.kept[made.id]["number"] == "4242424242424242"
    finally:
        await page.close()


async def test_the_eye_asks_the_daemon_each_time_it_shows_a_secret_and_hides_it_again_by_itself(
    browser: Browser, address: str, parts: tuple[Runtime, MemorySecretStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A screenshot of the Mac leaves out Dot's windows while a secret may be on the screen (item 621): the daemon
    knows of every show, since the eye asks it each time, and the page hides the secret again after twenty seconds. A
    text the owner changed is theirs: the eye shows it as it is."""
    runtime, _ = parts
    await _seed(runtime)
    monkeypatch.setattr(shown, "_until", 0.0)
    page: Page = await _open(browser, address)
    try:
        await page.clock.install()
        asked: list[str] = []
        page.on("request", lambda request: asked.append(request.url) if request.url.endswith("/reveal") else None)
        row: Locator = page.locator("[data-vault-item][data-kind=login]", has_text="github.com")
        dialog: Locator = await open_form(page, row.get_by_role("button", name="Edit github.com"), "Edit login")
        password: Locator = dialog.get_by_label("Password", exact=True)
        assert not shown.on_screen()
        await dialog.get_by_role("button", name="Show the password").click()
        await expect(password).to_have_value(PASSWORD)
        await expect(password).to_have_attribute("type", "text")
        assert len(asked) == 1 and shown.on_screen()
        # After twenty seconds the page hides it, and the eye offers to show it again.
        await page.clock.run_for(19_000)
        await expect(password).to_have_attribute("type", "text")
        await page.clock.run_for(2_000)
        await expect(password).to_have_attribute("type", "password")
        await expect(dialog.get_by_role("button", name="Show the password")).to_be_visible()
        # Showing it again asks again; hiding it by hand ends the wait, which then hides nothing.
        monkeypatch.setattr(shown, "_until", 0.0)
        await dialog.get_by_role("button", name="Show the password").click()
        await expect(password).to_have_attribute("type", "text")
        assert len(asked) == 2 and shown.on_screen()
        await dialog.get_by_role("button", name="Hide the password").click()
        await expect(password).to_have_attribute("type", "password")
        await dialog.get_by_role("button", name="Show the password").click()
        await expect(password).to_have_attribute("type", "text")
        await page.clock.run_for(5_000)
        await expect(password).to_have_attribute("type", "text")
        assert len(asked) == 3
        # A text the owner changed is kept as they wrote it, and the daemon is asked all the same.
        await dialog.get_by_role("button", name="Hide the password").click()
        await password.fill("changed-by-the-owner")
        await dialog.get_by_role("button", name="Show the password").click()
        await expect(password).to_have_attribute("type", "text")
        await expect(password).to_have_value("changed-by-the-owner")
        assert len(asked) == 4
    finally:
        await page.close()


async def test_a_secret_the_eye_showed_is_not_left_in_the_page_once_it_is_hidden(
    browser: Browser, address: str, parts: tuple[Runtime, MemorySecretStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chrome's accessibility tree gives the text a box holds, however it is drawn: dots made by a style hide it from
    the eye and not from anything that reads the tree, and a text hidden so stays in the page after its twenty seconds.
    A saved secret is in the page while it is shown alone: hiding it takes it out of the box and the next show asks the
    daemon again. A text drawn as dots is a password input, whose value the tree does not give; the notes, which cannot
    be one, hold no text while hidden and take none. What the owner changed is theirs and stays."""
    runtime, keychain = parts
    notes: str = "recovery word: marmalade-7731"
    await runtime.vault_items.create(
        LoginDraft(kind="login", website="github.com", username="ada", password=PASSWORD, notes=notes)  # type: ignore[arg-type]
    )
    await runtime.vault_items.create(
        CardDraft(kind="card", number=NUMBER, expiry="08/39", code="737", nickname="Travel")  # type: ignore[arg-type]
    )
    monkeypatch.setattr(shown, "_until", 0.0)
    page: Page = await _open(browser, address)
    try:
        await page.clock.install()
        asked: list[str] = []
        page.on("request", lambda request: asked.append(request.url) if request.url.endswith("/reveal") else None)
        row: Locator = page.locator("[data-vault-item][data-kind=login]", has_text="github.com")
        dialog: Locator = await open_form(page, row.get_by_role("button", name="Edit github.com"), "Edit login")
        password: Locator = dialog.get_by_label("Password", exact=True)
        box: Locator = dialog.get_by_label("Notes (optional)")
        # Hidden, the saved secrets are in no box, and the notes take no text until they are shown.
        await expect(password).to_have_attribute("type", "password")
        await expect(box).to_have_value("")
        assert await box.evaluate("(element) => element.readOnly") is True
        tree: str = await _tree(page)
        assert PASSWORD not in tree and "marmalade" not in tree
        # Shown, the tree gives them: the search is one that finds.
        await dialog.get_by_role("button", name="Show the password").click()
        await dialog.get_by_role("button", name="Show the notes").click()
        await expect(password).to_have_value(PASSWORD)
        await expect(box).to_have_value(notes)
        assert await box.evaluate("(element) => element.readOnly") is False
        tree = await _tree(page)
        assert PASSWORD in tree and "marmalade-7731" in tree
        # Twenty seconds later they are hidden by themselves and out of the page, the box and the tree.
        await page.clock.run_for(21_000)
        await expect(password).to_have_attribute("type", "password")
        await expect(password).to_have_value("")
        await expect(box).to_have_value("")
        tree = await _tree(page)
        assert PASSWORD not in tree and "marmalade" not in tree
        # Showing them again asks again, and they are as they were saved.
        assert len(asked) == 2
        await dialog.get_by_role("button", name="Show the password").click()
        await expect(password).to_have_value(PASSWORD)
        assert len(asked) == 3
        await dialog.get_by_role("button", name="Hide the password").click()
        await expect(password).to_have_value("")
        assert PASSWORD not in await _tree(page)
        # A change of the owner's in the notes stays while they are hidden, in the script and not in the page.
        await dialog.get_by_role("button", name="Show the notes").click()
        await expect(box).to_have_value(notes)
        await box.fill("changed notes 4455")
        await dialog.get_by_role("button", name="Hide the notes").click()
        await expect(box).to_have_value("")
        await expect(box).to_have_attribute("placeholder", "Your change is kept: press the eye to read it")
        tree = await _tree(page)
        assert "changed notes" not in tree and "marmalade" not in tree
        await dialog.get_by_role("button", name="Show the notes").click()
        await expect(box).to_have_value("changed notes 4455")
        await dialog.get_by_role("button", name="Hide the notes").click()
        await dialog.get_by_role("button", name="Save login").click()
        await expect(dialog).to_have_count(0)
        saved: list[dict[str, str]] = list(keychain.kept.values())
        assert {"password": PASSWORD, "notes": "changed notes 4455"} in saved, saved
        # A card's number: a password input while it is hidden, and nothing of a saved one is left in the page.
        card: Locator = page.locator("[data-vault-item][data-kind=card]", has_text="Travel")
        dialog = await open_form(page, card.get_by_role("button", name="Edit Travel"), "Edit card")
        number: Locator = dialog.get_by_label("Card number", exact=True)
        await expect(number).to_have_attribute("type", "password")
        await dialog.get_by_role("button", name="Show the card number").click()
        await expect(number).to_have_attribute("type", "text")
        await expect(number).to_have_value(NUMBER)
        assert "4242 4242 4242 4242" in await _tree(page)
        await dialog.get_by_role("button", name="Hide the card number").click()
        await expect(number).to_have_attribute("type", "password")
        await expect(number).to_have_value("")
        assert "4242 4242" not in await _tree(page)
        await dialog.get_by_role("button", name="Cancel").click()
        await expect(dialog).to_have_count(0)
        # A number the owner is writing is seen as it is typed, and drawn as a password once hidden.
        dialog = await _new_item(page, "Card")
        number = dialog.get_by_label("Card number", exact=True)
        await expect(number).to_have_attribute("type", "text")
        await number.fill("5555 5555 5555 4444")
        await dialog.get_by_role("button", name="Hide the card number").click()
        await expect(number).to_have_attribute("type", "password")
        await expect(number).to_have_value("5555 5555 5555 4444")
        assert "5555 5555" not in await _tree(page)
    finally:
        await page.close()


async def test_an_import_of_a_made_csv_says_what_it_did_and_to_delete_the_file(
    browser: Browser, address: str, parts: tuple[Runtime, MemorySecretStore]
) -> None:
    _, keychain = parts
    page: Page = await _open(browser, address)
    try:
        await page.get_by_role("button", name="New item").click()
        dialog: Locator = await open_form(
            page,
            page.get_by_role("menu", name="New item").get_by_role("menuitem", name="Import from CSV…"),
            "Import logins from a CSV file",
        )
        import_button: Locator = dialog.get_by_role("button", name="Import", exact=True)
        await expect(import_button).to_be_disabled()
        await dialog.get_by_label("CSV file").set_input_files(
            files=[{"name": "passwords.csv", "mimeType": "text/csv", "buffer": CSV}]
        )
        await expect(import_button).to_be_enabled()
        await import_button.click()
        await expect(dialog).to_have_count(0)
        report: Locator = page.locator(".vault-report")
        await expect(report).to_be_visible()
        await expect(report).to_contain_text("Imported 2 logins from Chrome or Edge's file.")
        await expect(report).to_contain_text("Left out 1 of 3 rows: not a web address (1).")
        await expect(report).to_contain_text("Delete the CSV file now: it holds your passwords in plain text.")
        await expect(page.locator("[data-vault-item][data-kind=login]")).to_have_count(2)
        await expect(_tab(page, "Logins, 2")).to_be_visible()
        assert sorted(parts_["password"] for parts_ in keychain.kept.values()) == [
            "imported-secret-1",
            "imported-secret-3",
        ]
        assert "imported-secret" not in await page.content()
        await report.get_by_role("button", name="Got it").click()
        await expect(report).to_be_hidden()
        # A file that is no list of logins is refused in the dialog, in words, and not quoted.
        await page.get_by_role("button", name="New item").click()
        dialog = await open_form(
            page,
            page.get_by_role("menu", name="New item").get_by_role("menuitem", name="Import from CSV…"),
            "Import logins from a CSV file",
        )
        await dialog.get_by_label("CSV file").set_input_files(
            files=[{"name": "other.csv", "mimeType": "text/csv", "buffer": b"a,b\n1,2\n"}]
        )
        await dialog.get_by_role("button", name="Import", exact=True).click()
        await shows_the_failure(dialog, "not a list of logins")
    finally:
        await page.close()


async def test_the_import_dialog_keeps_its_buttons_in_place_while_it_imports_and_its_file_field_is_a_field(
    browser: Browser, address: str
) -> None:
    """Item 812: the spinner put before Import's word made the button grow and Cancel move, and WebKit left traces of
    both, as if the buttons ran into each other; and the file field's button and name stood at the top of its box."""
    page: Page = await _open(browser, address)
    released: asyncio.Event = asyncio.Event()

    async def slow(route: Route) -> None:
        await released.wait()
        await route.continue_()

    try:
        await page.route("**/api/vault/items/import", slow)
        await page.get_by_role("button", name="New item").click()
        dialog: Locator = await open_form(
            page,
            page.get_by_role("menu", name="New item").get_by_role("menuitem", name="Import from CSV…"),
            "Import logins from a CSV file",
        )
        field: Locator = dialog.get_by_label("CSV file")
        # The button stands on the field's line, as tall as it, with the same room above and below inside the border.
        box = await field.bounding_box()
        line: str = await field.evaluate("(input) => getComputedStyle(input).lineHeight")
        # (A pixel or so of the font's own measure may come with the line.)
        assert box is not None and abs(box["height"] - (float(line.removesuffix("px")) + 2 * 4 + 2)) <= 1.5, (box, line)
        await field.set_input_files(files=[{"name": "passwords.csv", "mimeType": "text/csv", "buffer": CSV}])
        import_button: Locator = dialog.get_by_role("button", name="Import", exact=True)
        cancel: Locator = dialog.get_by_role("button", name="Cancel", exact=True)
        before = (await import_button.bounding_box(), await cancel.bounding_box())
        await import_button.click()
        await expect(import_button.locator(".spinner")).to_be_visible()
        await expect(import_button).to_have_class(re.compile(r"\bbusy\b"))
        during = (await import_button.bounding_box(), await cancel.bounding_box())
        assert during == before, (before, during)
        # The button keeps its name for a screen reader while its words are hidden under the spinner.
        await expect(import_button).to_have_accessible_name("Import")
        released.set()
        await expect(dialog).to_have_count(0)
    finally:
        released.set()
        await page.close()


def _card_photo() -> bytes:
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
    return buffer.getvalue()


async def test_a_photo_of_a_card_fills_the_form_for_the_owner_to_check(browser: Browser, address: str) -> None:
    page: Page = await _open(browser, address)
    try:
        dialog: Locator = await _new_item(page, "Card")
        await dialog.get_by_label("Photo of the card").set_input_files(
            files=[{"name": "card.png", "mimeType": "image/png", "buffer": _card_photo()}]
        )
        await expect(dialog.get_by_label("Card number", exact=True)).to_have_value(NUMBER, timeout=20_000)
        await expect(dialog.get_by_label("Expiry (MM / YY)")).to_have_value("12/39")
        await expect(dialog.get_by_role("textbox", name="Name on the card (optional)")).to_have_value("ADA LOVELACE")
        await expect(dialog.locator(".vault-photo-note")).to_contain_text("Check them before you save.")
        await expect(dialog.get_by_role("button", name="Save card")).to_be_enabled()
    finally:
        await page.close()


async def _jargon(page: Page, where: str) -> list[str]:
    """The words of the list of item 625 among the words a person sees on the page now, each with where it stands."""
    problems: list[str] = []
    for line in await page.evaluate(VISIBLE):
        for found in (*JARGON.finditer(line), *RUN_AS_A_THING.finditer(line)):
            problems.append(f"{where}: “{found.group(0).strip()}” in “{line[:110]}”")
    return problems


async def test_the_page_and_its_dialogs_say_what_they_do_in_words_someone_who_knows_no_software_reads(
    browser: Browser, address: str, parts: tuple[Runtime, MemorySecretStore]
) -> None:
    """Settings › Vault is read by the guard of item 625 (``test_plain_words.py`` leaves it to this test): the page, the
    menu of New item, each dialog and the question before a delete are opened, wide and on a phone, and the words a
    person sees are read: no ``daemon``, ``token``, ``tool``, ``server`` or a run said as a thing. A card is kept here,
    and the page says Dot does not fill it."""
    runtime, _ = parts
    await _seed(runtime)
    problems: list[str] = []
    for width in (1280, 390):
        page: Page = await _open(browser, address, width)
        try:
            problems += await _jargon(page, f"Settings › Vault ({width})")
            seen: str = " ".join(await page.evaluate(VISIBLE))
            assert "does not fill it into a page" in seen and "types a login into a page itself" in seen
            for name in ("Login", "Card", "Contact"):
                dialog: Locator = await _new_item(page, name)
                problems += await _jargon(page, f"New {name.lower()} ({width})")
                await page.keyboard.press("Escape")
                await expect(dialog).to_have_count(0)
            await page.get_by_role("button", name="New item").click()
            menu: Locator = page.get_by_role("menu", name="New item")
            await expect(menu).to_be_visible()
            importing: Locator = await open_form(
                page, menu.get_by_role("menuitem", name="Import from CSV…"), "Import logins from a CSV file"
            )
            problems += await _jargon(page, f"Import ({width})")
            await page.keyboard.press("Escape")
            await expect(importing).to_have_count(0)
            for kind, title in (("login", "github.com"), ("card", "Travel"), ("contact", "Home")):
                row: Locator = page.locator(f"[data-vault-item][data-kind={kind}]", has_text=title)
                dialog = await open_form(page, row.get_by_role("button", name=f"Edit {title}"), f"Edit {kind}")
                problems += await _jargon(page, f"Edit {kind} ({width})")
                await page.keyboard.press("Escape")
                await expect(dialog).to_have_count(0)
            card: Locator = page.locator("[data-vault-item][data-kind=card]", has_text="Travel")
            await card.get_by_role("button", name="Delete Travel").click()
            question: Locator = page.get_by_role("dialog", name="Delete this card?")
            await expect(question).to_be_visible()
            problems += await _jargon(page, f"Delete this card? ({width})")
            assert "can no longer sign in" not in await question.inner_text(), "a card was never signed in with"
            await question.get_by_role("button", name="Cancel").click()
        finally:
            await page.close()
    assert problems == [], "\n".join(problems)


async def test_on_a_phone_the_page_and_every_dialog_fit_the_window(
    browser: Browser, address: str, parts: tuple[Runtime, MemorySecretStore]
) -> None:
    runtime, _ = parts
    await _seed(runtime)
    page: Page = await _open(browser, address, 390, 844)
    try:
        await expect(page.locator("[data-vault-item]")).to_have_count(6)
        assert await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        for name in ("Login", "Card", "Contact"):
            dialog: Locator = await _new_item(page, name)
            await foot_is_in_view(page, dialog)
            await keeps_inside_the_window(page)
            await page.keyboard.press("Escape")
            await expect(dialog).to_have_count(0)
        row: Locator = page.locator("[data-vault-item][data-kind=card]", has_text="Travel")
        dialog = await open_form(page, row.get_by_role("button", name="Edit Travel"), "Edit card")
        await foot_is_in_view(page, dialog)
        await keeps_inside_the_window(page)
        await dialog.get_by_role("button", name="Cancel").click()
        # No box under 16 px where a phone zooms into it.
        small: list[str] = await page.evaluate(
            "() => [...document.querySelectorAll('.vault-page input, .vault-page select')]"
            ".filter((box) => box.getClientRects().length > 0)"
            ".filter((box) => parseFloat(getComputedStyle(box).fontSize) < 16).map((box) => box.outerHTML.slice(0, 80))"
        )
        assert small == [], small
    finally:
        await page.close()
