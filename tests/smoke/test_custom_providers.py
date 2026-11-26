"""Custom providers in Settings › AI services (item 1949): Add provider opens a form in a dialog that saves the
provider in the config file and in the running daemon, its model then listed beside the built-in providers'; Test makes
one call only when pressed and says how it failed; Change opens the form filled; Remove takes the provider out. The API
refuses a provider whose header carries a key and one a phone adds. A real server, wide and on a phone; the provider's
address is the daemon's own, so nothing reaches outside this Mac."""

import asyncio
import time
import tomllib
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from form_dialogs import foot_is_in_view, keeps_inside_the_window, open_form
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "custom-providers-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder: Path = tmp_path_factory.mktemp("home")
    (folder / "config.toml").write_text(
        "[browser]\nheadless = true\n\n[agent]\nplaybooks = false"
        f'\ngoal_mode = false\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n'
        f'review = "{SCRIPTED_MODEL}"\n\n[memory]\nembed_enabled = false\n',
        encoding="utf-8",
    )
    return folder


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(home: Path, tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    await built.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, []))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def served(runtime: Runtime) -> AsyncIterator[str]:
    manager: RunManager = RunManager(runtime)
    try:
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
    finally:
        await manager.shutdown()


async def _page(browser: Browser, address: str, width: int, scheme: Literal["light", "dark"]) -> tuple[Page, list[str]]:
    host, port = "127.0.0.1", int(address.rpartition(":")[2])
    page: Page = await browser.new_page(viewport={"width": width, "height": 900}, color_scheme=scheme)
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    await page.add_init_script("sessionStorage.setItem('dot.opened', '1')")
    await page.goto(ui_url(host, port, TOKEN))
    await page.goto(f"{address}/#/settings/keys")
    await page.wait_for_selector(".providers-layout", timeout=15000)
    return page, errors


def _custom(home: Path) -> dict[str, object]:
    table: object = tomllib.loads((home / "config.toml").read_text(encoding="utf-8")).get("providers", {})
    assert isinstance(table, dict)
    custom: object = table.get("custom", {})
    assert isinstance(custom, dict)
    return custom


async def test_a_provider_is_added_tested_changed_and_removed_from_the_form(
    runtime: Runtime, served: str, home: Path, tmp_path: Path
) -> None:
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch()
        try:
            for width, scheme, name in ((1280, "dark", "local-one"), (390, "light", "local-two")):
                page, errors = await _page(browser, served, width, scheme)
                opener: Locator = page.locator(".provider-add")
                dialog: Locator = await open_form(page, opener, "Add a provider")
                await keeps_inside_the_window(page)
                await foot_is_in_view(page, dialog)
                await dialog.get_by_label("Name", exact=True).fill(name)
                await dialog.get_by_label("Base URL", exact=True).fill(f"{served}/v1")
                await dialog.get_by_label("Protocol", exact=True).select_option("chat")
                # A server on this Mac takes no key: the key's field goes with the tick.
                await dialog.get_by_text("It takes an API key").click()
                await expect(dialog.get_by_label("Key", exact=True)).to_be_hidden()
                await dialog.get_by_role("button", name="Add model").click()
                await dialog.get_by_label("Model id").fill("tiny-1")
                await page.screenshot(path=str(tmp_path / f"form-{width}-{scheme}.png"))
                # Test asks the address once, and says how it failed: the daemon has no /v1/chat/completions.
                await dialog.get_by_role("button", name="Test").click()
                await expect(dialog.locator(".note.bad[role=status]")).to_contain_text("The test failed", timeout=30000)
                await expect(dialog.locator(".note.bad[role=status]")).to_contain_text("provider answered HTTP")
                await dialog.get_by_role("button", name="Save").click()
                await expect(dialog).to_be_hidden(timeout=15000)
                row: Locator = page.locator(".provider-item").filter(has_text=name)
                await expect(row).to_have_count(1)
                await expect(row.locator(".provider-line")).to_have_text("No key needed")
                # Saved, the provider's page is open, on a phone in place of the list.
                panel: Locator = page.locator(".provider-panel")
                await expect(panel.locator(".provider-title")).to_have_text(name)
                await expect(panel.locator(".custom-facts")).to_contain_text(f"{served}/v1")
                await expect(panel.locator(".provider-model[data-model]")).to_have_attribute(
                    "data-model", f"{name}:tiny-1", timeout=15000
                )
                assert name in runtime.router.custom
                assert _custom(home)[name] == runtime.router.custom[name].model_dump(mode="json")
                # Change opens the form filled with what is saved.
                changing: Locator = await open_form(page, panel.get_by_role("button", name="Change"), f"Change {name}")
                await expect(changing.get_by_label("Base URL", exact=True)).to_have_value(f"{served}/v1")
                await expect(changing.get_by_label("Model id")).to_have_value("tiny-1")
                await changing.get_by_label("Base URL", exact=True).fill(f"{served}/v2")
                await changing.get_by_role("button", name="Save").click()
                await expect(changing).to_be_hidden(timeout=15000)
                assert runtime.router.custom[name].base_url == f"{served}/v2"
                await page.locator(".provider-panel").get_by_role("button", name="Remove provider").click()
                await page.get_by_role("dialog").get_by_role("button", name="Remove").click()
                await expect(page.locator(".provider-item").filter(has_text=name)).to_have_count(0, timeout=15000)
                assert name not in runtime.router.custom and name not in _custom(home)
                assert errors == []
                await page.context.close()
        finally:
            await browser.close()


async def test_the_api_refuses_a_header_that_carries_a_key_and_a_built_in_name(served: str, runtime: Runtime) -> None:
    provider: dict[str, object] = {
        "base_url": "https://api.example-cloud.dev/v1",
        "protocol": "chat",
        "needs_key": True,
        "headers": {"X-Api-Key": "sk-should-not-be-kept"},
        "models": [{"id": "m"}],
        "list_models": False,
    }
    async with httpx.AsyncClient(base_url=served, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        refused = await client.put("/api/providers/custom/acme", json=provider)
        assert refused.status_code == 422
        assert "carries a secret" in refused.text and "sk-should-not-be-kept" not in refused.text
        built_in = await client.put("/api/providers/custom/openai", json={**provider, "headers": {}})
        assert built_in.status_code == 422
        saved = await client.put("/api/providers/custom/acme", json={**provider, "headers": {}})
        assert saved.status_code == 200
        assert saved.json()["config"]["api_key_env"] == "DOT_CUSTOM_ACME_API_KEY"
        keys = await client.get("/api/keys")
        assert {"provider": "acme", "name": "DOT_CUSTOM_ACME_API_KEY"}.items() <= next(
            view for view in keys.json()["keys"] if view["provider"] == "acme"
        ).items()
        # A saved key goes to the address it was saved for alone: Test elsewhere takes a typed key, and a new address
        # drops the saved one.
        assert (await client.post("/api/keys/DOT_CUSTOM_ACME_API_KEY", json={"value": "sk-saved-for-acme"})).is_success
        elsewhere = {**provider, "headers": {}, "base_url": "https://collector.example.net/v1"}
        tested = await client.post(
            "/api/providers/custom/test", json={"name": "acme", "provider": elsewhere, "key": None, "model": "m"}
        )
        assert tested.status_code == 422 and "no key is saved for this address" in tested.text
        moved = await client.put("/api/providers/custom/acme", json=elsewhere)
        assert moved.status_code == 200 and moved.json()["key_set"] is False
        acme = next(view for view in (await client.get("/api/keys")).json()["keys"] if view["provider"] == "acme")
        assert acme["saved"] is False
        # Nor through a table that stops taking a key and takes one again (the security review of item 1949).
        assert (await client.post("/api/keys/DOT_CUSTOM_ACME_API_KEY", json={"value": "sk-saved-for-acme"})).is_success
        keyless = await client.put("/api/providers/custom/acme", json={**elsewhere, "needs_key": False})
        assert keyless.status_code == 200
        later = {**elsewhere, "base_url": "https://other.example.org/v1"}
        tested = await client.post(
            "/api/providers/custom/test", json={"name": "acme", "provider": later, "key": None, "model": "m"}
        )
        assert tested.status_code == 422
        again = await client.put("/api/providers/custom/acme", json=later)
        assert again.status_code == 200 and again.json()["key_set"] is False
        removed = await client.delete("/api/providers/custom/acme")
        assert removed.status_code == 200
        # A key under that name kept outside Dot's .env is never attached to an address from here (its second review).
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("DOT_CUSTOM_ACME_API_KEY", "sk-from-the-environment")
            outside = await client.put("/api/providers/custom/acme", json={**provider, "headers": {}})
            assert outside.status_code == 422 and "not one Settings saved" in outside.text
    assert "acme" not in runtime.router.custom
