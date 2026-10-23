"""The installer's page (``desktop/ui/installer.html``, item 575), run in a browser with a stand-in for the shell: what
the Mac has, the choice of models and the key, the install's steps, and what it says when a step fails. The page is
served over HTTP from the folder the app bundles, with the content security policy of ``tauri.conf.json``, so that what
the app forbids is forbidden here too. The stand-in answers the five commands with what the shell would answer and
records each call; the real shell is tested in ``desktop/src-tauri``.

The key the person types goes to ``installer_start`` once and is never drawn: not by the page, and not when a program's
line that holds it comes back unmasked (the shell masks it; the page hides it a second time)."""

import functools
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Literal

import pytest
from playwright.async_api import Browser, Page, async_playwright, expect

from dotagent.config import load_default_config

_ROOT: Path = Path(__file__).resolve().parents[2] / "desktop"
_UI: Path = _ROOT / "ui"
_CSP: str = json.loads((_ROOT / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8"))["app"]["security"]["csp"]
_KEY: str = "sk-MARKER-0123456789abcdef"
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)
_PROVIDERS: list[dict[str, object]] = json.loads((_ROOT / "src-tauri" / "providers.json").read_text(encoding="utf-8"))


class _Handler(SimpleHTTPRequestHandler):
    """The folder the app bundles, with the policy the app puts on its pages."""

    def end_headers(self) -> None:
        self.send_header("Content-Security-Policy", _CSP)
        super().end_headers()

    def do_GET(self) -> None:
        # A browser asks for an icon nobody drew; the app's window asks for none.
        if self.path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()
            return
        super().do_GET()

    def log_message(self, format: str, *args: object) -> None:
        return None


@contextmanager
def _served() -> Iterator[str]:
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(_Handler, directory=str(_UI)))
    thread: threading.Thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/installer.html"
    finally:
        server.shutdown()
        server.server_close()


def _inspection(**changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "version": "0.1.0",
        "macos": {"ok": True, "detail": "macOS 26.0.1"},
        "chrome": {"ok": False, "detail": "the browser tools use it"},
        "ollama": {"running": True, "models": ["gpt-oss:20b", "qwen3:8b"]},
        "location": {"ok": True, "detail": "/Applications/Dot.app"},
        "providers": _PROVIDERS,
        "resuming": False,
        "modelsDone": False,
        "blocked": None,
        "log": "/Users/me/Library/Logs/Dot/installer.log",
    }
    return {**base, **changes}


def _step(step: str, title: str, state: str, output: list[str] | None = None) -> dict[str, object]:
    return {"id": step, "title": title, "state": state, "output": output or []}


_TITLES: tuple[tuple[str, str], ...] = (
    ("package", "Install Dot's engine"),
    ("models", "Set up the models"),
    ("service", "Start Dot at every login"),
    ("daemon", "Wait for Dot to answer"),
)


def _status(
    phase: str,
    states: list[str],
    lines: list[str] | None = None,
    failure: object = None,
    can_change_models: bool = True,
) -> dict[str, object]:
    return {
        "phase": phase,
        "canChangeModels": can_change_models,
        "steps": [
            _step(step, title, state, lines if state == "running" else None)
            for (step, title), state in zip(_TITLES, states, strict=True)
        ],
        "failure": failure,
        "log": "/Users/me/Library/Logs/Dot/installer.log",
    }


def _shell(
    inspection: dict[str, object],
    statuses: list[dict[str, object]],
    start_error: str | None = None,
    retry_error: str | None = None,
) -> str:
    """A script that stands in for the shell's bridge before the page runs: it answers the commands and keeps each call
    in ``window.__calls``. The statuses follow one another from the install's start; the last one stays."""
    scenario: str = json.dumps(
        {"inspection": inspection, "statuses": statuses, "startError": start_error, "retryError": retry_error}
    )
    return (
        "(() => {"
        f"const scenario = {scenario};"
        "const calls = []; let position = -1; window.__calls = calls;"
        "window.__TAURI__ = { core: { invoke: async (command, args) => {"
        "  calls.push({ command, args: args ?? null });"
        "  if (command === 'installer_inspect') return scenario.inspection;"
        "  if (command === 'installer_status') {"
        "    if (position < 0) return { phase: 'idle', steps: [], failure: null, log: '' };"
        "    const status = scenario.statuses[Math.min(position, scenario.statuses.length - 1)];"
        "    position += 1; return status; }"
        "  if (command === 'installer_start') {"
        "    if (scenario.startError) throw scenario.startError;"
        "    position = 0; return null; }"
        "  if (command === 'installer_retry') {"
        "    if (scenario.retryError) throw scenario.retryError;"
        "    position = 1; return null; }"
        "  return null; } } };"
        "})();"
    )


async def _calls(page: Page, command: str) -> list[dict[str, object]]:
    return [call for call in await page.evaluate("window.__calls") if call["command"] == command]


async def _everything_the_page_draws(page: Page) -> str:
    return await page.evaluate(
        "() => document.documentElement.outerHTML + document.body.innerText"
        " + JSON.stringify(Object.entries(localStorage)) + JSON.stringify(Object.entries(sessionStorage))"
        " + location.href"
    )


async def _fits(page: Page) -> bool:
    return bool(await page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))


async def _page(browser: Browser, width: int, scheme: Literal["light", "dark"], script: str | None) -> Page:
    page: Page = await browser.new_page(
        viewport={"width": width, "height": 800}, color_scheme=scheme, reduced_motion="reduce"
    )
    errors: list[str] = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    page._errors = errors
    if script is not None:
        await page.add_init_script(script)
    return page


@pytest.mark.browser
async def test_the_installer_leads_from_what_the_mac_has_to_an_install_that_ends_without_the_key_on_the_page(
    tmp_path: Path,
) -> None:
    """Chrome missing warns and offers its page; the models step shows a provider's key and model fields, keeps the
    install button off until both are right, and sends the choice once; the steps run to the end; the key is nowhere
    in the page, though a program's line that holds it came back unmasked, and its field is emptied at the end."""
    unmasked: list[str] = [f"error: the provider said {_KEY} is not a key"]
    statuses: list[dict[str, object]] = [
        _status("running", ["running", "waiting", "waiting", "waiting"], ["Resolved 72 packages"]),
        _status("running", ["done", "running", "waiting", "waiting"], unmasked),
        _status("done", ["done", "done", "done", "done"]),
    ]
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(
            channel=load_default_config().browser.channel, headless=True
        )
        try:
            with _served() as address:
                for width, scheme in _SHOTS:
                    page: Page = await _page(browser, width, scheme, _shell(_inspection(), statuses))
                    await page.goto(address)
                    await expect(page.locator("h1")).to_have_text("Set up Dot")
                    rows = page.locator("#mac-rows .row")
                    await expect(rows).to_have_count(2)
                    await expect(rows.nth(0)).to_contain_text("macOS 26.0.1")
                    await expect(rows.nth(1)).to_contain_text("Google Chrome is not installed")
                    await expect(page.locator("#mac-next")).to_be_enabled()
                    assert await _fits(page)
                    await page.screenshot(path=str(tmp_path / f"installer-mac-{width}-{scheme}.png"))
                    await rows.nth(1).get_by_role("button", name="Get Chrome").click()
                    assert (await _calls(page, "installer_open"))[0]["args"] == {"what": "chrome"}
                    await page.locator("#mac-next").click()

                    await expect(page.locator("#models-title")).to_be_visible()
                    install = page.locator("#install")
                    await expect(install).to_be_disabled()
                    await page.get_by_label("OpenAI", exact=False).first.check()
                    await expect(page.locator("#model")).to_have_value("gpt-5.5")
                    await expect(page.locator("#key")).to_have_attribute("type", "password")
                    await expect(page.locator("#models-problem")).to_have_text("Paste your OpenAI API key.")
                    await expect(install).to_be_disabled()
                    await page.locator("#key").fill("short")
                    await expect(install).to_be_disabled()
                    await page.locator("#key").fill(f"  {_KEY}  ")
                    await expect(install).to_be_enabled()
                    await page.locator("#model").fill("--no-service")
                    await expect(install).to_be_disabled()
                    await page.locator("#model").fill("gpt-5.5")
                    assert await _fits(page)
                    await page.screenshot(path=str(tmp_path / f"installer-models-{width}-{scheme}.png"))
                    await install.click()

                    await expect(page.locator("#install-title")).to_have_text("Dot is ready")
                    await expect(page.locator("#steps .step[data-state='done']")).to_have_count(4)
                    await expect(page.locator("#done")).to_be_visible()
                    started = await _calls(page, "installer_start")
                    assert [call["args"] for call in started] == [
                        {"choice": {"provider": "openai", "model": "gpt-5.5", "key": _KEY}}
                    ]
                    assert _KEY not in await _everything_the_page_draws(page)
                    await expect(page.locator("#key")).to_have_value("")
                    assert await _fits(page)
                    await page.screenshot(path=str(tmp_path / f"installer-done-{width}-{scheme}.png"))
                    assert page._errors == []
                    await page.close()
        finally:
            await browser.close()


@pytest.mark.browser
async def test_a_failed_step_says_which_with_what_it_printed_and_try_again_asks_the_shell_to_go_on() -> None:
    """The failure shows the step, the shell's words, the lines it printed and what to do; the key the person typed is
    hidden even in those; Try again calls ``installer_retry``, Change the models goes back to the choice, and the log
    can be shown."""
    failure: dict[str, object] = {
        "step": "models",
        "title": "Set up the models",
        "message": "dotx setup stopped with exit code 2.",
        "output": [f"error: openai refused the key {_KEY}", "fix: check the key"],
        "fix": "Go back to check the model and the key, or press Try again.",
    }
    failed: dict[str, object] = _status("failed", ["done", "failed", "waiting", "waiting"], failure=failure)
    statuses: list[dict[str, object]] = [failed, _status("done", ["done", "done", "done", "done"])]
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(
            channel=load_default_config().browser.channel, headless=True
        )
        try:
            with _served() as address:
                for width, scheme in _SHOTS[::2]:
                    page: Page = await _page(browser, width, scheme, _shell(_inspection(), statuses))
                    await page.goto(address)
                    await page.locator("#mac-next").click()
                    await page.get_by_label("OpenAI", exact=False).first.check()
                    await page.locator("#key").fill(_KEY)
                    await page.locator("#install").click()
                    await expect(page.locator("#install-title")).to_have_text("The install stopped")
                    await expect(page.locator("#failure")).to_be_visible()
                    await expect(page.locator("#failure-title")).to_have_text("Set up the models did not finish")
                    await expect(page.locator("#failure-message")).to_have_text("dotx setup stopped with exit code 2.")
                    await expect(page.locator("#failure-output")).to_contain_text("openai refused the key ••••")
                    await expect(page.locator("#failure-fix")).to_contain_text("press Try again")
                    await expect(page.locator("#steps .step[data-state='failed']")).to_have_count(1)
                    assert _KEY not in await _everything_the_page_draws(page)
                    assert await _fits(page)
                    await page.get_by_role("button", name="Show the log").click()
                    assert (await _calls(page, "installer_open"))[-1]["args"] == {"what": "log"}
                    await page.get_by_role("button", name="Change the models").click()
                    await expect(page.locator("#models-title")).to_be_visible()
                    await expect(page.locator("#key")).to_have_value(_KEY)
                    await page.locator("#install").click()
                    await expect(page.locator("#failure")).to_be_visible()
                    await page.get_by_role("button", name="Try again").click()
                    await expect(page.locator("#install-title")).to_have_text("Dot is ready")
                    assert len(await _calls(page, "installer_retry")) == 1
                    assert page._errors == []
                    await page.close()
        finally:
            await browser.close()


@pytest.mark.browser
async def test_an_install_that_stopped_after_the_models_goes_on_without_asking_for_them_again() -> None:
    """With the models set up already, Continue asks the shell to go on (``installer_retry``) and the page shows the
    steps: it never shows the models screen and never sends ``installer_start``, so that the shell is given no choice to
    run ``dotx setup`` over the config with. A stopped install whose models are not done asks for them as a first
    install does, and a shell that refuses to go on is heard on the screen the person is looking at."""
    statuses: list[dict[str, object]] = [
        # Not read: ``installer_retry`` of the stand-in goes to the second status.
        _status("failed", ["done", "done", "failed", "waiting"]),
        _status("running", ["done", "done", "running", "waiting"], ["Installed the launchd job"]),
        _status("done", ["done", "done", "done", "done"]),
    ]
    service_failed: dict[str, object] = {
        "step": "service",
        "title": "Start Dot at every login",
        "message": "dotx service install stopped with exit code 1.",
        "output": ["error: the launchd job is loaded but the daemon does not answer"],
        "fix": "Press Try again. If it fails again, the log has what launchd and Dot printed.",
    }
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(
            channel=load_default_config().browser.channel, headless=True
        )
        try:
            with _served() as address:
                resuming = _inspection(resuming=True, modelsDone=True)
                for width, scheme in _SHOTS[::2]:
                    page: Page = await _page(browser, width, scheme, _shell(resuming, statuses))
                    await page.goto(address)
                    await expect(page.locator("#resuming")).to_be_visible()
                    await expect(page.locator("#resuming-models")).to_be_visible()
                    assert await _fits(page)
                    await page.locator("#mac-next").click()
                    await expect(page.locator("#install-title")).to_have_text("Dot is ready")
                    await expect(page.locator("#steps .step[data-state='done']")).to_have_count(4)
                    assert len(await _calls(page, "installer_retry")) == 1
                    assert await _calls(page, "installer_start") == []
                    await expect(page.locator("#models-title")).to_be_hidden()
                    assert await _fits(page)
                    assert page._errors == []
                    await page.close()

                # The resumed install's service step fails: the models were set up before this session and none was
                # chosen in it, so that Change the models is not offered (the shell would refuse a choice), and Try
                # again is. Where the session made the choice, the first failure test above has the button.
                kept = [
                    _status("failed", ["done", "done", "failed", "waiting"]),
                    _status(
                        "failed",
                        ["done", "done", "failed", "waiting"],
                        failure=service_failed,
                        can_change_models=False,
                    ),
                ]
                page = await _page(browser, 390, "light", _shell(resuming, kept))
                await page.goto(address)
                await page.locator("#mac-next").click()
                await expect(page.locator("#failure-title")).to_have_text("Start Dot at every login did not finish")
                await expect(page.get_by_role("button", name="Try again")).to_be_visible()
                await expect(page.get_by_role("button", name="Show the log")).to_be_visible()
                await expect(page.get_by_role("button", name="Change the models")).to_be_hidden()
                assert await _fits(page)
                await page.close()

                # Only the package is installed: the models are asked for, and nothing is gone on with.
                package_only = _inspection(resuming=True, modelsDone=False)
                page = await _page(browser, 1280, "light", _shell(package_only, statuses))
                await page.goto(address)
                await expect(page.locator("#resuming")).to_be_visible()
                await expect(page.locator("#resuming-models")).to_be_hidden()
                await page.locator("#mac-next").click()
                await expect(page.locator("#models-title")).to_be_visible()
                assert await _calls(page, "installer_retry") == []
                await page.close()

                # The shell refuses (a Dot appeared meanwhile): the words are on the screen the person is on.
                refused = "Dot is installed already: a Dot daemon already answers on this Mac."
                page = await _page(browser, 1280, "light", _shell(resuming, statuses, retry_error=refused))
                await page.goto(address)
                await page.locator("#mac-next").click()
                await expect(page.locator("#mac-problem")).to_have_text(refused)
                await expect(page.locator("#mac-title")).to_be_visible()
                await expect(page.locator("#install-title")).to_be_hidden()
                await page.close()
        finally:
            await browser.close()


@pytest.mark.browser
async def test_ollama_that_is_not_running_is_said_and_a_dot_that_is_there_stops_the_page() -> None:
    """With no Ollama running the install cannot go on with Ollama's models and the page says why and links to Ollama;
    with a Dot installed already, or the app on its disk image, the page says so and does not offer to go on. Opened in
    a browser outside the app, the page says it is the app's."""
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(
            channel=load_default_config().browser.channel, headless=True
        )
        try:
            with _served() as address:
                no_ollama = _inspection(ollama={"running": False, "models": []}, chrome={"ok": True, "detail": ""})
                page: Page = await _page(browser, 390, "light", _shell(no_ollama, []))
                await page.goto(address)
                await page.locator("#mac-next").click()
                await page.get_by_label("Ollama", exact=False).first.check()
                await expect(page.locator("#ollama-note")).to_contain_text("Ollama is not running")
                await expect(page.locator("#ollama-note")).to_contain_text("open Ollama and sign in")
                await expect(page.locator("#install")).to_be_disabled()
                await expect(page.locator("#models-problem")).to_have_text(
                    "Ollama is not running. Open it, then press Check again."
                )
                await page.locator("#ollama-note").get_by_role("button", name="Get Ollama").click()
                assert (await _calls(page, "installer_open"))[0]["args"] == {"what": "ollama"}
                assert await _fits(page)
                await page.close()

                there = _inspection(blocked="a Dot daemon already answers on this Mac")
                page = await _page(browser, 1280, "dark", _shell(there, []))
                await page.goto(address)
                await expect(page.locator("#mac-rows .row[data-state='block']")).to_contain_text(
                    "A Dot daemon already answers on this Mac, so there is nothing to set up"
                )
                await expect(page.locator("#mac-next")).to_be_disabled()
                await page.close()

                image = _inspection(location={"ok": False, "detail": "/Volumes/Dot/Dot.app"})
                page = await _page(browser, 1280, "light", _shell(image, []))
                await page.goto(address)
                await expect(page.locator("#mac-rows .row[data-state='block']")).to_contain_text(
                    "Drag Dot to the Applications folder"
                )
                await expect(page.locator("#mac-next")).to_be_disabled()
                await page.close()

                page = await _page(browser, 1280, "light", None)
                await page.goto(address)
                await expect(page.locator("#outside")).to_be_visible()
                await page.close()
        finally:
            await browser.close()
