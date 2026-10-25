"""A run's scripted fill of a login from Dot's own vault (item 621), through the browser tools and the gate, with a
stand-in for the Keychain and a real headless Chrome; no model is called. (A card is kept in the vault and is not
filled into a page: that is another item's.)

A login is filled on its own site and nowhere else, only into a field that is a form field, after the owner's yes in
every mode but yolo. The page shows none of it: the fields Dot filled are hidden in every view, a screenshot covers
them, and no event, approval, log line, message or database file of the run holds the password.

What Dot does by what a password is (marking a field again, refusing a request a page sends whose address carries it)
is done to the documents of the origin it was typed into and to no other, and never to the run itself: a frame or a
page of another origin, in any tab, finds nothing marked and no request refused, and the run, whose own navigations
and typing are judged by no value, learns nothing from a refusal (a list of 1,640 words did, in 22 calls)."""

import asyncio
import dataclasses
import json
import re
import shutil
import ssl
import subprocess
import tempfile
import threading
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

import pytest
import pytest_asyncio
import structlog
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Frame, Page
from vault_stand_ins import MemorySecretStore

from dotagent.agent.gate import RunGate, SiteLocks
from dotagent.agent.manager import RunManager
from dotagent.agent.review import ReviewRequest, ReviewVerdict
from dotagent.agent.verify import new_facts
from dotagent.browser import session as browser_session
from dotagent.browser.driver import _remark_frame
from dotagent.config import load_config
from dotagent.core.errors import BrowserError, ForbiddenUrlError, PolicyDenied, ToolInputError
from dotagent.core.logs import configure_logging
from dotagent.core.paths import env_file_path, workspace_dir
from dotagent.core.types import JsonObject
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, Message, ModelInfo, ModelReply
from dotagent.policy.types import ApprovalMode
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.secrets import known_secrets, write_env_value
from dotagent.server import UI_SOURCE
from dotagent.store.models import ApprovalRecord, RunRecord, ThreadRecord
from dotagent.tools.base import PreparedCall, ToolResult
from dotagent.tools.context import ToolContext
from dotagent.tools.meta import FINISH_TOOL
from dotagent.vault.items import CardDraft, LoginDraft, VaultItems

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

PASSWORD: str = "S3cret-Passw0rd!"
WEAK: str = "Summer2024!"
_ANSWER_WITHIN_S: float = 30.0
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_POLL_S: float = 0.05

_LOGIN: str = """<!doctype html><title>Sign in</title><h1>Sign in</h1>
<form action="/received" method="post">
<label>Email <input name="user" autocomplete="username"></label>
<label>Password <input name="pass" type="password" autocomplete="current-password"></label>
<button type="submit">Sign in</button></form>{extra}"""
# A page that gives the fields back what they held when it is loaded again, as a site's own script may (it keeps them in
# the window's name, which is in no file of the profile's but its session's), and a link away.
_RESTORING: str = """<!doctype html><title>Restoring</title><h1>Restoring</h1>
<form action="/received" method="post">
<label>Email <input name="user" autocomplete="username"></label>
<label>Password <input name="pass" type="password" autocomplete="current-password"></label>
<label>Comment <input name="comment" value="a note"></label>
<button type="submit">Sign in</button></form><a href="/other">Other page</a>
<script>
const kept = JSON.parse(window.name || '{}');
for (const input of document.querySelectorAll('input')) {
  if (kept[input.name] !== undefined) input.value = kept[input.name];
  input.addEventListener('input', () => {
    kept[input.name] = input.value;
    window.name = JSON.stringify(kept);
  });
}
</script>"""
_OTHER: str = """<!doctype html><title>Other</title><h1>Other</h1><a href="/signin">Back to sign in</a>"""
# A page that sends the tab to the address its query names, as a redirector or a page of a stranger may.
_BOUNCE: str = """<!doctype html><title>Bounce</title><script>
location.href = new URLSearchParams(location.search).get('to');</script>"""
# A password field in no form, and what a script of the page does with its value: it sends it in a fetch, or it sends
# the tab to an address that holds it.
_SCRIPTED: str = """<!doctype html><title>Scripted</title><h1>Scripted</h1>
<label>Password <input name="pass" type="password" autocomplete="current-password"></label>
<button type="button" onclick="fetch('/found?p=' + encodeURIComponent(document.querySelector('[name=pass]').value))
  .then(() => { document.title = 'sent'; }, () => { document.title = 'refused'; })">Send by fetch</button>
<button type="button"
onclick="location.href = '/found?p=' + encodeURIComponent(document.querySelector('[name=pass]').value)">
Send by address</button>"""
# A form of the page that sends what is typed in it by GET: the run types in it, and the page sends what it typed.
_SEARCH: str = """<!doctype html><title>Search</title><h1>Search</h1>
<form action="/found" method="get"><label>Query <input name="q"></label><button type="submit">Go</button></form>"""
# A password input whose label holds a div: the AI snapshot gives the div a ref of its own, and the fill follows the
# label to the input. The form sends by GET: its method says so, or it names none (a browser sends it by GET).
_LABELLED: str = """<!doctype html><title>Labelled</title><h1>Labelled</h1>
<form action="/found" {method}><label><div>Secret phrase</div>
<input name="pass" type="password" autocomplete="current-password"></label><button type="submit">Go</button></form>"""
# Fields that are no form field: an element that is edited, a box a script draws, the same inside a shadow root, and a
# password input inside one (which is a form field like any other).
_EDITABLE: str = """<!doctype html><title>Editable</title><h1>Editable</h1>
<div id="code" contenteditable="true" role="textbox" aria-label="Code"></div>
<div id="bare" contenteditable="true" aria-label="Bare box"></div>
<label>Password <input id="real" name="pass" type="password" autocomplete="current-password"></label>
<div id="host"></div>
<script>
  const root = document.getElementById('host').attachShadow({ mode: 'open' });
  root.innerHTML = '<div id="inner" contenteditable="true" role="textbox" aria-label="Inner code"></div>'
    + '<label>Inner password <input id="secret" name="shadow" type="password"></label>';
</script>"""
# What a page of any other origin can do with a guard that acts by what was filled: try addresses, and see which of them
# the browser refused (``search`` halves a list of candidates until one is left).
_TRY: str = """
window.blocked = async (list) => {
  try { await fetch('/probe?c=' + list.join(';')); return false; } catch (error) { return true; }
};
window.search = async (list) => {
  let requests = 1;
  if (!(await window.blocked(list))) return { found: null, requests };
  while (list.length > 1) {
    const half = list.slice(0, list.length >> 1);
    requests += 1;
    list = (await window.blocked(half)) ? half : list.slice(half.length);
  }
  return { found: list[0], requests };
};"""
# A third party's widget in a frame of the sign-in page, an origin that the password was never typed into. It plants
# fields that hold the values a password might have, counts how often anything looks at its fields, and reads which of
# them were marked.
_WIDGET: str = (
    """<!doctype html><title>Widget</title><body>
<script>
window.looked = 0;
const asked = Document.prototype.querySelectorAll;
Document.prototype.querySelectorAll = function (selector) {
  if (selector === 'input, textarea, select') window.looked += 1;
  return asked.call(this, selector);
};
window.plant = (values) => {
  document.body.replaceChildren();
  for (const value of values) {
    const input = document.createElement('input');
    input.value = value;
    document.body.append(input);
  }
};
window.marked = () => [...document.querySelectorAll('input')]
  .filter((input) => input.hasAttribute('data-dot-vault') || input.getAttribute('autocomplete') === 'off')
  .map((input) => input.value);"""
    + _TRY
    + "</script>"
)
_ATTACKER: str = "<!doctype html><title>Another site</title><h1>Another site</h1><script>" + _TRY + "</script>"


class _Site(ThreadingHTTPServer):
    """A site: its pages by path, what each POST sent, and every address a GET asked for, kept."""

    def __init__(self, pages: dict[str, str]) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.pages: dict[str, str] = pages
        self.submissions: list[str] = []
        self.requested: list[str] = []

    @property
    def port(self) -> int:
        return int(self.server_address[1])


class _Handler(BaseHTTPRequestHandler):
    server: _Site

    def _send(self, status: int, body: str) -> None:
        data: bytes = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        self.server.requested.append(self.path)
        path: str = self.path.split("?", 1)[0]
        if path == "/received":
            self.server.submissions.append(self.path)
            self._send(200, "<!doctype html><title>Received</title><h1>Received</h1>")
        else:
            page: str | None = self.server.pages.get(path)
            self._send(200 if page is not None else 404, page or "not found")

    def do_POST(self) -> None:
        self.server.submissions.append(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode())
        self._send(200, "<!doctype html><title>Received</title><h1>Received</h1>")

    def log_message(self, format: str, *args: object) -> None:
        return


def _certificate(folder: Path) -> tuple[Path, Path]:
    """A certificate of its own for 127.0.0.1 and localhost, made with the Mac's openssl."""
    key, cert = folder / "key.pem", folder / "cert.pem"
    subprocess.run(
        [
            "/usr/bin/openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "2",
            "-subj",
            "/CN=127.0.0.1",
            "-addext",
            "subjectAltName=IP:127.0.0.1,DNS:localhost",
        ],
        check=True,
        capture_output=True,
    )
    return key, cert


class _Sites:
    """The sites of the test: the https shop, which has the sign-in pages (and answers under any host of
    ``example.localhost``, which Chrome reaches on this Mac), the https provider whose frames the shop holds (on
    ``localhost``, an origin of its own), and the plain http site that has a login."""

    def __init__(self, folder: Path) -> None:
        key, cert = _certificate(folder)
        self.provider: _Site = _Site({"/widget": _WIDGET, "/attacker": _ATTACKER})
        self.shop: _Site = _Site({})
        widget: str = f"https://localhost:{self.provider.port}/widget"
        near: str = f"https://b.example.localhost:{self.shop.port}/widget"
        self.shop.pages = {
            "/signin": _LOGIN.format(extra=""),
            "/signin-widget": _LOGIN.format(extra=f'<iframe title="A third party" src="{widget}"></iframe>'),
            "/signin-frames": _LOGIN.format(extra=f'<iframe title="Another host" src="{near}"></iframe>'),
            "/widget": _WIDGET,
            "/restoring": _RESTORING,
            "/scripted": _SCRIPTED,
            "/search": _SEARCH,
            "/editable": _EDITABLE,
            "/labelled-get": _LABELLED.format(method='method="get"'),
            "/labelled-bare": _LABELLED.format(method=""),
            "/labelled-post": _LABELLED.format(method='method="post"'),
            "/other": _OTHER,
            "/bounce": _BOUNCE,
            "/attacker": _ATTACKER,
        }
        self.plain: _Site = _Site({"/login": _LOGIN.format(extra=""), "/attacker": _ATTACKER})
        for secure in (self.provider, self.shop):
            context: ssl.SSLContext = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(cert, key)
            # The handshake of a connection is done in the thread that serves it, not in the loop that accepts: a
            # connection that opens and says nothing holds up no other (a page's first load timed out once on a Mac
            # that other work was loading).
            secure.socket = context.wrap_socket(secure.socket, server_side=True, do_handshake_on_connect=False)
        for site in (self.provider, self.shop, self.plain):
            threading.Thread(target=site.serve_forever, daemon=True).start()

    def close(self) -> None:
        for site in (self.provider, self.shop, self.plain):
            site.shutdown()
            site.server_close()


@pytest.fixture(scope="module")
def sites() -> Iterator[_Sites]:
    folder: Path = Path(tempfile.mkdtemp(prefix="dot-vault-certificate-"))
    made: _Sites = _Sites(folder)
    try:
        yield made
    finally:
        made.close()
        shutil.rmtree(folder, ignore_errors=True)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def parts(
    tmp_path_factory: pytest.TempPathFactory, sites: _Sites
) -> AsyncIterator[tuple[Runtime, MemorySecretStore, Path]]:
    """A runtime whose browser has no window and accepts the certificate of the test's sites, whose vault keeps its
    secrets in a dictionary, and whose log goes to a file of its own; the folder of the home."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text("[browser]\nheadless = true\n", encoding="utf-8")
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    patch.setattr(browser_session, "_LAUNCH_ARGS", [*browser_session._LAUNCH_ARGS, "--ignore-certificate-errors"])
    configure_logging("debug", True, home / "dot.log")
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    keychain: MemorySecretStore = MemorySecretStore()
    runtime: Runtime = dataclasses.replace(built, vault_items=VaultItems(built.store, keychain))
    try:
        yield runtime, keychain, home
    finally:
        await close_runtime(built)
        patch.undo()
        structlog.reset_defaults()


async def _nobody(request: ReviewRequest) -> ReviewVerdict:
    raise AssertionError(f"the review model must not be asked about this: {request.action.summary}")


async def _context(runtime: Runtime, approvals: ApprovalMode) -> tuple[ToolContext, RunGate]:
    """A run of ``approvals`` in the store, its gate and the context its tools get."""
    thread: ThreadRecord = await runtime.store.create_thread("vault fill", "test")
    run: RunRecord = await runtime.store.create_run(
        thread.id, "Sign in.", "task", "ollama:none", None, "test", "medium", "standard", None, approvals,
        False, None, None, None,
    )  # fmt: skip
    gate: RunGate = RunGate(
        runtime.store,
        runtime.bus,
        runtime.approvals,
        run.id,
        "task",
        "aggressive",
        runtime.config.policy.pace,
        runtime.config.policy.spend,
        approvals,
        _nobody,
        ("Sign in.",),
        SiteLocks(),
    )
    ctx: ToolContext = ToolContext(
        runtime=runtime,
        browser=runtime.browser,
        run_id=run.id,
        agent_id=None,
        thread_id=thread.id,
        mode="task",
        approvals=approvals,
        questions="wait",
        workspace=workspace_dir(),
        gate=gate,
        cancel=asyncio.Event(),
        output_chars=runtime.config.agent.tool_output_chars,
        facts=new_facts,
    )
    return ctx, gate


async def _call(ctx: ToolContext, gate: RunGate, name: str, arguments: JsonObject) -> ToolResult:
    """A call as the executor makes it: classified, authorised with the arguments the model wrote (the gate reads them
    for a secret a tainted run would send, and the action holds them masked), then run."""
    prepared: PreparedCall = await ctx.runtime.registry.tools[name].prepare(arguments, ctx)
    await gate.authorize_recorded(prepared.action, arguments)
    return await prepared.run(ctx)


async def _refs(ctx: ToolContext, gate: RunGate, url: str, labels: dict[str, str]) -> dict[str, str]:
    """Open ``url`` and find the ref of the field called each label."""
    opened: ToolResult = await _call(ctx, gate, "browser_open", {"url": url})
    refs: dict[str, str] = {}
    for name, label in labels.items():
        match: re.Match[str] | None = re.search(rf'"{re.escape(label)}"[^\n]*?\[ref=([^\]]+)\]', opened.text)
        assert match is not None, (name, opened.text)
        refs[name] = match.group(1)
    return refs


def _page(runtime: Runtime, thread_id: str) -> Page:
    """The page of the thread's first tab, to look at what the site's own page holds."""
    return runtime.browser._threads[thread_id].tabs[0].page


async def _widget(page: Page) -> Frame:
    """The third party's frame of the sign-in page, once its page has loaded."""
    async with asyncio.timeout(_ANSWER_WITHIN_S):
        while True:
            for frame in page.frames:
                if not frame.url.endswith("/widget"):
                    continue
                try:
                    if await frame.evaluate("typeof plant === 'function'"):
                        return frame
                except PlaywrightError:
                    continue  # the frame's page is not there yet
            await asyncio.sleep(_POLL_S)


async def _asked(runtime: Runtime, run_id: str) -> ApprovalRecord:
    """The question the run ``run_id`` is waiting on."""
    async with asyncio.timeout(_ANSWER_WITHIN_S):
        while True:
            pending: list[ApprovalRecord] = [
                record
                for record in await runtime.store.list_approvals(None, 50)
                if record.run_id == run_id and record.status == "pending"
            ]
            if pending:
                return pending[0]
            await asyncio.sleep(_POLL_S)


async def _vault(runtime: Runtime) -> VaultItems:
    vault: VaultItems = runtime.vault_items
    for record in await vault.items():
        await vault.remove(record.id)
    return vault


async def _a_login(
    vault: VaultItems,
    sites: _Sites,
    password: str = PASSWORD,
    website: str | None = None,
    username: str = "ada@example.com",
) -> str:
    """A login of the vault for the shop (its https origin), and its id."""
    made = await vault.create(
        LoginDraft(
            kind="login",
            website=website or f"127.0.0.1:{sites.shop.port}",
            username=username,
            password=password,  # type: ignore[arg-type]
        )
    )
    return made.id


def _reached(site: _Site, value: str) -> bool:
    """Whether an address that holds ``value``, as it is or escaped, was asked of the site."""
    return any(value in path or quote(value, safe="") in path for path in site.requested)


def _button(view: str, name: str) -> str:
    found: re.Match[str] | None = re.search(rf'button "{re.escape(name)}"[^\n]*?\[ref=([^\]]+)\]', view)
    assert found is not None, view
    return found.group(1)


def _link(view: str, name: str) -> str:
    found: re.Match[str] | None = re.search(rf'link "{re.escape(name)}"[^\n]*?\[ref=([^\]]+)\]', view)
    assert found is not None, view
    return found.group(1)


async def _signed(
    runtime: Runtime, sites: _Sites, approvals: ApprovalMode = "yolo", path: str = "signin", password: str = PASSWORD
) -> tuple[ToolContext, RunGate, str, dict[str, str]]:
    """A run on the shop's sign-in page with the login of the vault filled in, and the refs of its fields."""
    vault: VaultItems = await _vault(runtime)
    login: str = await _a_login(vault, sites, password)
    ctx, gate = await _context(runtime, approvals)
    refs: dict[str, str] = await _refs(
        ctx, gate, f"https://127.0.0.1:{sites.shop.port}/{path}", {"user": "Email", "pass": "Password"}
    )
    await _fill(runtime, ctx, gate, approvals, {"login": login, "username": refs["user"], "password": refs["pass"]})
    return ctx, gate, login, refs


async def _fill(
    runtime: Runtime, ctx: ToolContext, gate: RunGate, approvals: ApprovalMode, arguments: JsonObject
) -> ToolResult:
    """The fill a run asks for, with the owner's yes to it in a mode that asks."""
    if approvals == "yolo":
        return await _call(ctx, gate, "browser_fill_login", arguments)
    filling: asyncio.Task[ToolResult] = asyncio.create_task(_call(ctx, gate, "browser_fill_login", arguments))
    asked: ApprovalRecord = await _asked(runtime, ctx.run_id)
    await runtime.approvals.resolve(asked.id, True, UI_SOURCE, "")
    return await filling


async def test_a_run_fills_a_login_of_dots_vault_on_its_own_site_and_no_view_or_record_shows_the_password(
    parts: tuple[Runtime, MemorySecretStore, Path], sites: _Sites
) -> None:
    runtime, keychain, _ = parts
    vault: VaultItems = await _vault(runtime)
    made = await vault.create(
        LoginDraft(
            kind="login",
            website=f"http://127.0.0.1:{sites.plain.port}/login",
            username="ada@example.com",
            password=PASSWORD,  # type: ignore[arg-type]
        )
    )
    other = await vault.create(LoginDraft(kind="login", website="https://example.org", password="x" * 12))  # type: ignore[arg-type]
    ctx, gate = await _context(runtime, "yolo")
    refs: dict[str, str] = await _refs(
        ctx, gate, f"http://127.0.0.1:{sites.plain.port}/login", {"user": "Email", "pass": "Password"}
    )
    listed: ToolResult = await _call(ctx, gate, "browser_logins", {})
    assert f"{made.id}: 127.0.0.1:{sites.plain.port} · ada@example.com" in listed.text, listed.text
    assert f"{other.id}: example.org, in Dot's vault" in listed.text
    assert PASSWORD not in listed.text and "ada@example.com" in listed.text
    filled: ToolResult = await _call(
        ctx, gate, "browser_fill_login", {"login": made.id, "username": refs["user"], "password": refs["pass"]}
    )
    assert "Filled the username, password of the login" in filled.text and "from Dot's vault" in filled.text
    for text in (filled.text,):
        assert PASSWORD not in text and "ada@example.com" not in text
    # A login of another site is refused before anything is asked, and so is a one-time code: the vault keeps none.
    with pytest.raises(ToolInputError, match="fills a login only on its own site"):
        await _call(ctx, gate, "browser_fill_login", {"login": other.id, "password": refs["pass"]})
    with pytest.raises(ToolInputError, match="keeps no one-time codes"):
        await _call(ctx, gate, "browser_fill_login", {"login": made.id, "otp": refs["user"]})
    # The page holds the values and shows none of them: its fields are hidden in every view, whatever the mode.
    seen: ToolResult = await _call(ctx, gate, "browser_snapshot", {})
    assert seen.text.count("[hidden]") == 2 and PASSWORD not in seen.text and "ada@example.com" not in seen.text
    with pytest.raises(BrowserError, match="scripts stay off"):
        await _call(ctx, gate, "browser_eval", {"script": "document.querySelector('input').value"})
    await _call(ctx, gate, "browser_act", {"steps": [{"action": "click", "ref": _button(seen.text, "Sign in")}]})
    assert any("S3cret-Passw0rd" in body for body in sites.plain.submissions), sites.plain.submissions
    assert keychain.kept[made.id] == {"password": PASSWORD}


async def test_a_card_is_kept_and_listed_by_no_tool_and_filled_by_none(
    parts: tuple[Runtime, MemorySecretStore, Path], sites: _Sites
) -> None:
    """A card is kept in the vault, with its number in the Keychain; a run has no tool that lists it or fills it, and
    no tool of the browser takes a part of a card."""
    runtime, _, _ = parts
    vault: VaultItems = await _vault(runtime)
    await vault.create(CardDraft(kind="card", number="4242 4242 4242 4242", expiry="08/39", nickname="Travel"))  # type: ignore[arg-type]
    names: set[str] = set(runtime.registry.tools)
    assert not {"browser_cards", "browser_fill_card"} & names
    ctx, gate = await _context(runtime, "yolo")
    listed: ToolResult = await _call(ctx, gate, "browser_logins", {})
    assert "4242" not in listed.text and "Travel" not in listed.text


async def test_a_password_goes_only_into_an_input_a_textarea_or_a_dropdown_and_not_into_an_element_that_is_edited(
    parts: tuple[Runtime, MemorySecretStore, Path], sites: _Sites
) -> None:
    """An element that is edited (``contenteditable``) or that a script draws as a box (``role=textbox``) takes typed
    text, shows it in the text of the page, and has no autocomplete to keep Chrome from storing it: a password goes into
    none of them, also in a shadow root; an input in a shadow root is a form field like another."""
    runtime, _, _ = parts
    vault: VaultItems = await _vault(runtime)
    login: str = await _a_login(vault, sites)
    ctx, gate = await _context(runtime, "yolo")
    refs: dict[str, str] = await _refs(
        ctx,
        gate,
        f"https://127.0.0.1:{sites.shop.port}/editable",
        {"code": "Code", "bare": "Bare box", "inner": "Inner code", "secret": "Inner password", "real": "Password"},
    )
    page: Page = _page(runtime, ctx.thread_id)
    for name in ("code", "bare", "inner"):
        # Every part of a login goes into a form field alone: the username too, which no mark would hide there.
        for part in ("password", "username"):
            with pytest.raises(BrowserError, match="is not a form field") as refused:
                await _call(ctx, gate, "browser_fill_login", {"login": login, part: refs[name]})
            assert PASSWORD not in str(refused.value) and "ada@example.com" not in str(refused.value)
    # Nothing was typed: the elements are empty, and nothing is held back for the chat.
    for element in ("#code", "#bare", "#inner"):
        assert await page.locator(element).inner_text() == "", element
    assert not runtime.browser.holds_back(ctx.thread_id)
    # A password input inside a shadow root is typed into, and is hidden in the view like any field Dot filled.
    await _call(ctx, gate, "browser_fill_login", {"login": login, "password": refs["secret"]})
    assert await page.locator("#secret").input_value() == PASSWORD
    seen: ToolResult = await _call(ctx, gate, "browser_snapshot", {})
    assert PASSWORD not in seen.text and "[hidden]" in seen.text, seen.text


def _ref_in(view: str, pattern: str) -> str:
    """The ref the first line of ``view`` that ``pattern`` finds names."""
    found: re.Match[str] | None = re.search(pattern, view)
    assert found is not None, view
    return found.group(1)


async def _tried_by_the_labels_div(
    runtime: Runtime, sites: _Sites, secret: str, path: str = "labelled-get"
) -> tuple[ToolContext, RunGate]:
    """A run that gives the ref of the div inside the label of a password input of a form that sends by GET (the ref of
    the input itself too, which is refused) and then clicks Go, as a run that was told to would."""
    login: str = await _a_login(await _vault(runtime), sites, secret)
    ctx, gate = await _context(runtime, "yolo")
    opened: ToolResult = await _call(ctx, gate, "browser_open", {"url": f"https://127.0.0.1:{sites.shop.port}/{path}"})
    on_input: str = _ref_in(opened.text, r'textbox "Secret phrase" \[ref=([^\]]+)\]')
    on_div: str = _ref_in(opened.text, r"generic \[ref=([^\]]+)\]: Secret phrase")
    for ref in (on_input, on_div):
        with pytest.raises(BrowserError, match="sends what is typed in the page's address"):
            await _call(ctx, gate, "browser_fill_login", {"login": login, "password": ref})
    # Nothing was typed, so nothing is held back for the chat.
    assert await _page(runtime, ctx.thread_id).locator("[name=pass]").input_value() == ""
    assert not runtime.browser.holds_back(ctx.thread_id)
    seen: ToolResult = await _call(ctx, gate, "browser_snapshot", {})
    await _call(ctx, gate, "browser_act", {"steps": [{"action": "click", "ref": _button(seen.text, "Go")}]})
    return ctx, gate


@pytest.mark.parametrize("path", ["labelled-get", "labelled-bare"])
async def test_a_ref_inside_the_label_of_a_password_input_is_refused_in_a_form_that_sends_by_get_as_the_inputs_own_is(
    parts: tuple[Runtime, MemorySecretStore, Path], sites: _Sites, path: str
) -> None:
    """The AI snapshot gives a div, a span or a b in a label a ref of its own, and Playwright's fill writes the label's
    input. The form was asked of the element the ref named, a div that has none, so the password went into a form that
    sends by GET (or names no method) and a click on Go wrote it into the tab's address. The field is the control of
    the label, found once, and the ref of the input and the ref of the div are refused alike, with nothing typed."""
    runtime, _, _ = parts
    secret: str = f"Zq9-Label-Get-{path}"
    ctx, _ = await _tried_by_the_labels_div(runtime, sites, secret, path)
    page: Page = _page(runtime, ctx.thread_id)
    assert secret not in page.url and quote(secret, safe="") not in page.url
    assert not _reached(sites.shop, secret), sites.shop.requested
    assert any(requested.startswith("/found?pass=") for requested in sites.shop.requested), "the form was sent empty"


async def test_a_ref_inside_the_label_of_a_password_input_fills_the_input_and_marks_it_and_only_it_in_a_form_that_posts(
    parts: tuple[Runtime, MemorySecretStore, Path], sites: _Sites
) -> None:
    """Where the form keeps the password out of the address, the ref of the div in the label is the input's: the input
    takes the password, and it is the input that is marked, covered in a picture and hidden in the view (the mark went
    on the div, which holds no value, when the ref was read as the element it named)."""
    runtime, _, _ = parts
    secret: str = "Zq9-Label-Post-Pw"
    login: str = await _a_login(await _vault(runtime), sites, secret)
    ctx, gate = await _context(runtime, "yolo")
    opened: ToolResult = await _call(
        ctx, gate, "browser_open", {"url": f"https://127.0.0.1:{sites.shop.port}/labelled-post"}
    )
    on_div: str = _ref_in(opened.text, r"generic \[ref=([^\]]+)\]: Secret phrase")
    await _call(ctx, gate, "browser_fill_login", {"login": login, "password": on_div})
    page: Page = _page(runtime, ctx.thread_id)
    assert await page.locator("[name=pass]").input_value() == secret
    assert await page.locator("[name=pass]").get_attribute("data-dot-vault") == ""
    assert await page.locator("[name=pass]").get_attribute("autocomplete") == "off"
    assert await page.locator("label div").get_attribute("data-dot-vault") is None
    seen: ToolResult = await _call(ctx, gate, "browser_snapshot", {})
    assert secret not in seen.text and "[hidden]" in seen.text, seen.text


async def test_the_password_a_ref_inside_a_label_could_not_put_in_the_address_is_in_no_file_of_chromes_profile(
    parts: tuple[Runtime, MemorySecretStore, Path], sites: _Sites
) -> None:
    """Chrome's History and its url database keep the address of a page after Chrome is closed, and Dot deletes only the
    session files: a password in the address of the tab stays in the profile (row 13 of the contract)."""
    runtime, _, _ = parts
    secret: str = "Zq9Wn7Lm2Kd4"
    await _tried_by_the_labels_div(runtime, sites, secret)
    profile: Path = runtime.browser.profile_dir
    await runtime.browser.let_go_of_chrome()
    assert await asyncio.to_thread(_in_profile, profile, {"password": secret}) == []


async def test_the_password_a_ref_inside_a_label_could_not_put_in_the_address_is_in_no_record_of_the_run(
    parts: tuple[Runtime, MemorySecretStore, Path], sites: _Sites
) -> None:
    """What the run read of the page after the click, its events and messages, the database file and the log beside
    it: none holds the password (row 1 of the contract)."""
    runtime, _, home = parts
    secret: str = "Zq9Wn7Lm2Kd5"
    ctx, gate = await _tried_by_the_labels_div(runtime, sites, secret)
    again: ToolResult = await _call(ctx, gate, "browser_snapshot", {})
    assert secret not in again.text and quote(secret, safe="") not in again.text
    async with runtime.store._db.execute("PRAGMA wal_checkpoint(TRUNCATE)") as cursor:
        await cursor.fetchall()
    await runtime.browser.let_go_of_chrome()
    needles: list[bytes] = [secret.encode(), secret.encode("utf-16-le"), quote(secret, safe="").encode()]
    assert await asyncio.to_thread(_holding, home, needles) == []
    assert await asyncio.to_thread(_holding, runtime.browser.profile_dir, needles) == []


@pytest.mark.parametrize("approvals", ["ask", "yolo"])
async def test_a_page_that_gives_the_password_back_after_going_back_has_it_marked_again(
    parts: tuple[Runtime, MemorySecretStore, Path], sites: _Sites, approvals: ApprovalMode
) -> None:
    """A page whose own script gives the fields what they held brings the password back into a new document, without
    Dot's mark: a field that holds a value Dot filled is marked again before a view or a picture is made of the page, so
    that the view hides it. A field with another text is left as it was."""
    runtime, _, _ = parts
    ctx, gate, _, _ = await _signed(runtime, sites, approvals, "restoring")
    page: Page = _page(runtime, ctx.thread_id)
    seen: ToolResult = await _call(ctx, gate, "browser_snapshot", {})
    await _call(ctx, gate, "browser_act", {"steps": [{"action": "click", "ref": _link(seen.text, "Other page")}]})
    back: ToolResult = await _call(ctx, gate, "browser_back", {})
    # The page gave the values back to its fields, in a document that never had Dot's mark.
    assert await page.locator("[name=pass]").input_value() == PASSWORD
    assert PASSWORD not in back.text and "ada@example.com" not in back.text
    # The password's field is marked again and says [hidden]; the username is no secret to hide, and is masked as text.
    assert back.text.count("[hidden]") == 1 and "a note" in back.text, back.text
    field = page.locator("[name=pass]")
    assert await field.get_attribute("data-dot-vault") == ""
    assert await field.get_attribute("autocomplete") == "off"
    assert await page.locator("[name=comment]").get_attribute("data-dot-vault") is None


async def test_a_frame_of_an_origin_the_password_was_not_typed_into_is_not_looked_into_and_cannot_read_what_was_marked(
    parts: tuple[Runtime, MemorySecretStore, Path], sites: _Sites
) -> None:
    """Dot marks again, before every view and picture, a field of a later document that holds a value it filled. A frame
    of another site, which plants fields that hold the candidates and reads which of them were marked, would learn a
    password it was never given: Dot looks into the documents of the origin the value was typed into and into no other,
    so the frame finds nothing marked and nothing of it was read."""
    runtime, _, _ = parts
    ctx, gate, _, _ = await _signed(runtime, sites, path="signin-widget")
    page: Page = _page(runtime, ctx.thread_id)
    widget: Frame = await _widget(page)
    assert widget.url.startswith(f"https://localhost:{sites.provider.port}/")
    await widget.evaluate("(values) => plant(values)", [PASSWORD, "S3cret Passw0rd", "Winter1990", "S3cret-Passw0rd"])
    for tool in ("browser_snapshot", "browser_screenshot"):
        await _call(ctx, gate, tool, {})
    assert await widget.evaluate("marked()") == []
    assert await widget.evaluate("window.looked") == 0
    # The page the password was typed into is looked into, and keeps its mark.
    assert await page.locator("[name=pass]").get_attribute("data-dot-vault") == ""


async def test_a_document_of_another_origin_of_the_same_login_is_not_looked_into_either(
    parts: tuple[Runtime, MemorySecretStore, Path], sites: _Sites
) -> None:
    """A login names a site, and every host under it is one the password may be typed on. The password was typed into
    the field of one document, of one origin: a frame of another host under the same site is another document, one the
    password never reached, and Dot runs nothing in it and refuses it no request (N3: a value is scoped to the
    document of its own field, not to every site the login names)."""
    runtime, _, _ = parts
    vault: VaultItems = await _vault(runtime)
    login: str = await _a_login(vault, sites, website=f"example.localhost:{sites.shop.port}")
    ctx, gate = await _context(runtime, "yolo")
    near: str = f"https://a.example.localhost:{sites.shop.port}"
    refs: dict[str, str] = await _refs(ctx, gate, f"{near}/signin-frames", {"user": "Email", "pass": "Password"})
    await _call(ctx, gate, "browser_fill_login", {"login": login, "username": refs["user"], "password": refs["pass"]})
    page: Page = _page(runtime, ctx.thread_id)
    widget: Frame = await _widget(page)
    assert widget.url.startswith(f"https://b.example.localhost:{sites.shop.port}/")
    await widget.evaluate("(values) => plant(values)", [PASSWORD, "S3cret Passw0rd", "Winter1990"])
    for tool in ("browser_snapshot", "browser_screenshot"):
        await _call(ctx, gate, tool, {})
    assert await widget.evaluate("marked()") == []
    assert await widget.evaluate("window.looked") == 0
    # Its requests are held to no password either: it never had it. The page that has it is refused the same fetch.
    sending: str = (
        "async (word) => { try { await fetch('/probe?p=' + word); return 'sent'; } catch { return 'refused'; } }"
    )
    assert await widget.evaluate(sending, PASSWORD) == "sent"
    assert await page.evaluate(sending, PASSWORD) == "refused"


async def test_a_frame_is_not_touched_by_a_script_that_finds_it_in_another_document_than_the_one_dot_read(
    parts: tuple[Runtime, MemorySecretStore, Path], sites: _Sites
) -> None:
    """A frame that moves between Dot's reading of its address and the script (N1) is a frame Dot has not looked at: the
    script is told the address Dot read and touches no field of a document that is another. The page is the shop's, its
    field holds the password; told the address of another document, the script marks nothing and reads nothing, and told
    its own, it marks the field."""
    runtime, _, _ = parts
    ctx, _, _, _ = await _signed(runtime, sites, path="signin-widget")
    page: Page = _page(runtime, ctx.thread_id)
    widget: Frame = await _widget(page)
    await widget.evaluate("(values) => plant(values)", [PASSWORD])
    wanted: frozenset[str] = frozenset({"s3cretpassw0rd"})
    await _remark_frame(widget, f"https://localhost:{sites.provider.port}/elsewhere", wanted)
    assert await widget.evaluate("marked()") == [] and await widget.evaluate("window.looked") == 0
    await _remark_frame(widget, widget.url, wanted)
    assert await widget.evaluate("marked()") == [PASSWORD] and await widget.evaluate("window.looked") == 1
    # The page the password was typed into: an address that is not its own touches nothing of it; its own marks again.
    field = page.locator("[name=comment]")
    assert await field.count() == 0
    await page.locator("[name=user]").evaluate("(e) => { e.removeAttribute('data-dot-vault'); }")
    await page.locator("[name=pass]").evaluate("(e) => { e.removeAttribute('data-dot-vault'); }")
    await _remark_frame(page.main_frame, "https://127.0.0.1/elsewhere", wanted)
    assert await page.locator("[name=pass]").get_attribute("data-dot-vault") is None
    await _remark_frame(page.main_frame, page.main_frame.url, wanted)
    assert await page.locator("[name=pass]").get_attribute("data-dot-vault") == ""


async def test_requests_a_page_of_another_origin_sends_are_judged_by_nothing_that_was_filled(
    parts: tuple[Runtime, MemorySecretStore, Path], sites: _Sites
) -> None:
    """A request a page sends whose address holds the password is refused, for good (item 493). Refused to the page the
    password was typed into alone: a page of another origin, in another chat, in another tab or in a frame of that page,
    that tried addresses and saw which were refused would learn a weak password in a dozen requests."""
    runtime, _, _ = parts
    vault: VaultItems = await _vault(runtime)
    login: str = await _a_login(vault, sites, WEAK)
    ctx, gate = await _context(runtime, "yolo")
    refs: dict[str, str] = await _refs(
        ctx, gate, f"https://127.0.0.1:{sites.shop.port}/signin-widget", {"user": "Email", "pass": "Password"}
    )
    await _call(ctx, gate, "browser_fill_login", {"login": login, "username": refs["user"], "password": refs["pass"]})
    shop: Page = _page(runtime, ctx.thread_id)
    sending: str = (
        "async (word) => { try { await fetch('/probe?p=' + word); return 'sent'; } catch { return 'refused'; } }"
    )
    # The page the password was typed into is refused the address, which is what the guard is for.
    assert await shop.evaluate(sending, WEAK) == "refused"
    assert await shop.evaluate(sending, "Winter1990") == "sent"
    assert not any("Summer2024" in path for path in sites.shop.requested), "a refused request is never sent"
    words: list[str] = ["Winter", "Spring", "Summer", "Autumn", "Password", "Welcome", "Qwerty", "Dragon", "Monkey"]
    dictionary: list[str] = [
        f"{word}{year}{suffix}" for word in words for year in range(1990, 2031) for suffix in ("", "!", "1", "@")
    ]
    assert WEAK in dictionary
    # A page of another origin in another chat.
    other, other_gate = await _context(runtime, "yolo")
    await _call(other, other_gate, "browser_open", {"url": f"http://127.0.0.1:{sites.plain.port}/attacker"})
    attacker: Page = _page(runtime, other.thread_id)
    assert (await attacker.evaluate("(list) => search(list)", dictionary))["found"] is None
    # A frame of another origin inside the page the password was typed into.
    widget: Frame = await _widget(shop)
    assert (await widget.evaluate("(list) => search(list)", dictionary))["found"] is None
    # The same tab, gone on to a page of another origin.
    await _call(ctx, gate, "browser_open", {"url": f"https://localhost:{sites.provider.port}/attacker"})
    assert (await shop.evaluate("(list) => search(list)", dictionary))["found"] is None


# The words a run tries in a script of its own (``browser_eval``): 1,640, the password among them.
_CANDIDATES: list[str] = [
    f"{season}{year}{sign}"
    for season in ("Winter", "Spring", "Summer", "Autumn")
    for year in range(1980, 2030)
    for sign in ("", "!", "#")
]
_CANDIDATES += [f"word{number:04d}x" for number in range(1640 - len(_CANDIDATES))]
# The script: one request with every word, then the half of the list that was refused, until one word is left.
_HALVING: str = """(async (words) => {
  let requests = 0;
  const refused = async (list) => {
    requests += 1;
    try { await fetch('/probe?c=' + encodeURIComponent(list.join(';'))); return false; } catch (error) { return true; }
  };
  if (!(await refused(words))) return { found: null, requests };
  let list = words;
  while (list.length > 1) {
    const half = list.slice(0, list.length >> 1);
    list = (await refused(half)) ? half : list.slice(half.length);
  }
  return { found: list[0], requests };
})(%s)"""


async def _refused_in(page: Page, word: str) -> bool:
    """Whether the browser refuses a request of ``page``'s own script that carries ``word`` in its address."""
    script: str = "(word) => fetch('/probe?c=' + encodeURIComponent(word)).then(() => false, () => true)"
    return bool(await page.evaluate(script, word))


async def _halved(ctx: ToolContext, gate: RunGate) -> tuple[str | None, int]:
    """What a script of the run's own finds out in the page its chat is at, by which of its requests the browser
    refuses: the word it ends with (``None`` when the first request, which carries every word, was not refused) and how
    many requests it sent."""
    ran: ToolResult = await _call(
        ctx, gate, "browser_eval", {"script": _HALVING % json.dumps(_CANDIDATES), "timeout_s": 30}
    )
    found: re.Match[str] | None = re.search(r'"found":\s*(null|"([^"\\]*)")', ran.text)
    sent: re.Match[str] | None = re.search(r'"requests":\s*(\d+)', ran.text)
    assert found is not None and sent is not None, ran.text
    return found.group(2), int(sent.group(1))


@pytest.mark.parametrize("approvals", ["yolo", "ask"])
async def test_a_script_of_another_chat_in_a_page_of_the_same_origin_finds_no_password_by_the_requests_refused(
    parts: tuple[Runtime, MemorySecretStore, Path], sites: _Sites, approvals: ApprovalMode
) -> None:
    """Chat A fills a login and keeps its tab. Chat B, which filled nothing, opens a page of the same origin that holds
    no secret field and runs a script of its own: no request of it is refused, the one that carries the password and one
    that carries another word alike (the guard judged every document of the origin in any tab by the value, and the
    script had a weak password in twelve requests out of 1,640 words, with no typing and no mask). In the tab the
    password was typed into, where scripts stay off, the guard still refuses it."""
    runtime, _, _ = parts
    assert WEAK in _CANDIDATES and len(_CANDIDATES) == 1640
    chat, _, _, _ = await _signed(runtime, sites, "yolo", password=WEAK)
    filled: Page = _page(runtime, chat.thread_id)
    other, other_gate = await _context(runtime, approvals)
    await _call(other, other_gate, "browser_open", {"url": f"https://127.0.0.1:{sites.shop.port}/other"})
    # The control: where the password was typed a request that carries it is refused and one that carries a word of its
    # shape is not, which is what the script of the other chat would read if it were held to the value.
    assert await _refused_in(filled, WEAK) and not await _refused_in(filled, "Summer2025!")
    assert await _halved(other, other_gate) == (None, 1)
    assert await _refused_in(filled, WEAK), "the tab the password was typed into is still held to it"


@pytest.mark.parametrize("approvals", ["yolo", "ask"])
async def test_the_chat_that_filled_a_login_finds_no_password_by_the_requests_refused_once_its_tab_is_closed(
    parts: tuple[Runtime, MemorySecretStore, Path], sites: _Sites, approvals: ApprovalMode
) -> None:
    """While the tab it filled is open the chat runs no script. Once that tab is closed, a new tab of the same chat at
    the same origin is a tab nothing was typed into, and its script is held to no value."""
    runtime, _, _ = parts
    chat, gate, _, _ = await _signed(runtime, sites, approvals, password=WEAK)
    with pytest.raises(BrowserError, match="scripts stay off"):
        await _call(chat, gate, "browser_eval", {"script": "1 + 1"})
    await _call(chat, gate, "browser_close_tab", {"index": 1})
    await _call(chat, gate, "browser_open", {"url": f"https://127.0.0.1:{sites.shop.port}/other"})
    assert await _halved(chat, gate) == (None, 1)


async def _named_in_the_refusal(runtime: Runtime, ctx: ToolContext, gate: RunGate, sites: _Sites, word: str) -> str:
    """The address a run reads in the refusal when a page sends its tab to Dot's own interface with ``word`` in the
    query (which no run opens), as the page of a stranger may."""
    inside: str = quote(f"http://127.0.0.1:{runtime.config.server.port}/x?c={quote(word, safe='')}", safe="")
    address: str = f"https://127.0.0.1:{sites.shop.port}/bounce?to={inside}"
    with pytest.raises(ForbiddenUrlError) as refused:
        await _call(ctx, gate, "browser_open", {"url": address})
    return str(refused.value)


async def test_the_words_a_refusal_names_an_address_with_tell_a_chat_nothing_of_a_password_another_chat_filled(
    parts: tuple[Runtime, MemorySecretStore, Path], sites: _Sites
) -> None:
    """A page can send a tab to an address its sender chose, and the refusal names it in words that change when it
    carries a password (its host alone). Made from every chat's passwords, those words told a run in a chat that filled
    nothing whether a word of its own was the password of another chat, and a list of words in the address, halved,
    found a weak one. A chat reads them made from what it filled itself."""
    runtime, _, _ = parts
    chat, gate, _, _ = await _signed(runtime, sites, "yolo", password=WEAK)
    other, other_gate = await _context(runtime, "yolo")
    right: str = await _named_in_the_refusal(runtime, other, other_gate, sites, WEAK)
    wrong: str = await _named_in_the_refusal(runtime, other, other_gate, sites, "Summer2025!")
    assert quote(WEAK, safe="") in right and quote("Summer2025!", safe="") in wrong
    assert right.replace(quote(WEAK, safe=""), "<word>") == wrong.replace(quote("Summer2025!", safe=""), "<word>")
    # The control: the chat that filled it reads its own password named by the host alone.
    mine: str = await _named_in_the_refusal(runtime, chat, gate, sites, WEAK)
    assert "[an address that carries a filled value]" in mine and quote(WEAK, safe="") not in mine


async def test_the_run_finds_no_password_by_the_addresses_it_opens_or_the_forms_it_types_in(
    parts: tuple[Runtime, MemorySecretStore, Path], sites: _Sites
) -> None:
    """The run's own navigations are judged by no value filled: it cannot carry a value it never saw, and a refusal by a
    value would tell it whether a guess is the password (the review found one in 22 calls from a list of 1,640 words).
    It opens addresses that list candidates from the tab the password is in, and types a list into a form of the page
    that sends it: nothing is refused, in the page's tab or in any other."""
    runtime, _, _ = parts
    vault: VaultItems = await _vault(runtime)
    login: str = await _a_login(vault, sites, WEAK)
    ctx, gate = await _context(runtime, "ask")
    refs: dict[str, str] = await _refs(
        ctx, gate, f"https://127.0.0.1:{sites.shop.port}/signin", {"user": "Email", "pass": "Password"}
    )
    await _fill(runtime, ctx, gate, "ask", {"login": login, "username": refs["user"], "password": refs["pass"]})
    words: list[str] = ["Winter", "Spring", "Summer", "Autumn", "Password", "Welcome", "Qwerty", "Dragon", "Monkey"]
    dictionary: list[str] = [
        f"{word}{year}{suffix}" for word in words for year in range(1990, 2031) for suffix in ("", "!", "1", "@")
    ]
    assert WEAK in dictionary
    # An address the policy lets through is short: the run opens the list a hundred words at a time (the review's binary
    # search took 22 calls).
    chunks: list[list[str]] = [dictionary[start : start + 100] for start in range(0, len(dictionary), 100)]
    calls: int = 0
    refused: list[bool] = []
    for tab in ("the page's own tab", "another chat"):
        run, run_gate = (ctx, gate) if tab == "the page's own tab" else await _context(runtime, "ask")
        for candidates in (*chunks, [WEAK], ["Winter1990"]):
            calls += 1
            address: str = f"https://127.0.0.1:{sites.shop.port}/signin?c={';'.join(candidates)}"
            try:
                await _call(run, run_gate, "browser_open", {"url": address})
                refused.append(False)
            except ForbiddenUrlError:
                refused.append(True)
    assert refused == [False] * calls, "an address the run opens is judged by the rules and by no value"
    assert _reached(sites.shop, f"c={WEAK}") or _reached(sites.shop, WEAK), "the addresses were asked of the site"
    # The run types a list into a form of a page that sends it by GET: the page's navigation is no request the
    # guard holds.
    fields: dict[str, str] = await _refs(ctx, gate, f"https://127.0.0.1:{sites.shop.port}/search", {"q": "Query"})
    holding: list[str] = next(chunk for chunk in chunks if WEAK in chunk)
    typed: ToolResult = await _call(
        ctx,
        gate,
        "browser_act",
        {
            "steps": [
                {"action": "fill", "ref": fields["q"], "text": ";".join(holding)},
                {"action": "press", "ref": fields["q"], "key": "Enter"},
            ]
        },
    )
    assert "/found?q=" in _page(runtime, ctx.thread_id).url, typed.text
    assert WEAK not in typed.text, "the views mask it"
    assert any(path.startswith("/found?q=") for path in sites.shop.requested)


async def test_a_page_that_builds_an_address_from_the_password_is_not_let_send_it_in_a_request_of_its_own(
    parts: tuple[Runtime, MemorySecretStore, Path], sites: _Sites
) -> None:
    """The guard is for what a page sends: a script that puts the password into a fetch is refused, and the request
    never reaches the site. The tab's own address is the run's: a page that sends the tab to an address that holds the
    password is not stopped (the guard would tell the run a guess, as it told it by the addresses it opened), and what
    the run is shown of that address is masked."""
    runtime, _, _ = parts
    vault: VaultItems = await _vault(runtime)
    # A password of its own: an address that holds it is in Chrome's history, which the test of the traces searches.
    secret: str = "Sc0ped-Passw0rd-Zq9!"
    login: str = await _a_login(vault, sites, secret)
    ctx, gate = await _context(runtime, "yolo")
    refs: dict[str, str] = await _refs(ctx, gate, f"https://127.0.0.1:{sites.shop.port}/scripted", {"pass": "Password"})
    await _call(ctx, gate, "browser_fill_login", {"login": login, "password": refs["pass"]})
    seen: ToolResult = await _call(ctx, gate, "browser_snapshot", {})
    await _call(ctx, gate, "browser_act", {"steps": [{"action": "click", "ref": _button(seen.text, "Send by fetch")}]})
    assert await _page(runtime, ctx.thread_id).title() == "refused"
    assert not _reached(sites.shop, secret), sites.shop.requested
    sent: ToolResult = await _call(
        ctx, gate, "browser_act", {"steps": [{"action": "click", "ref": _button(seen.text, "Send by address")}]}
    )
    assert _reached(sites.shop, secret), "the tab's own address is the run's"
    assert secret not in sent.text and quote(secret, safe="") not in sent.text, sent.text


async def test_a_step_of_a_run_that_read_outside_content_is_refused_for_the_secrets_it_could_see_and_not_for_a_password(
    parts: tuple[Runtime, MemorySecretStore, Path], sites: _Sites
) -> None:
    """A step of a run that read outside content is refused when it would send a secret Dot holds out of the Mac
    (item 525). A password a vault filled is not among them: the run never saw it, so refusing the guess that is right
    would tell it the password (13 calls from a list of 1,640 words in a tainted run). A key the owner keeps is still
    refused."""
    runtime, _, _ = parts
    # A password of its own: the addresses the test opens are in Chrome's history, which the traces' test searches.
    guessed: str = "T1-Guess-Passw0rd!"
    ctx, gate, _, _ = await _signed(runtime, sites, password=guessed)
    gate.taint()
    assert guessed in known_secrets(filled=True) and guessed not in known_secrets(filled=False), (
        "masked, and only masked"
    )
    kept: str = env_file_path().read_text(encoding="utf-8") if env_file_path().exists() else ""
    owners_key: str = "correct-horse-battery-staple"
    write_env_value(env_file_path(), "DEMO_SERVICE_KEY", owners_key)
    try:
        address: str = f"https://127.0.0.1:{sites.shop.port}/probe?c="
        # Guesses of the run, the right one among them: none is refused.
        for guess in (guessed, "Winter1990", f"Winter1990;{guessed};Spring1991"):
            await _call(ctx, gate, "browser_open", {"url": address + guess})
        # The control: a key Dot holds that the run could see is still refused, so that the check is one that finds.
        with pytest.raises(PolicyDenied, match="send a secret"):
            await _call(ctx, gate, "browser_open", {"url": address + owners_key})
    finally:
        env_file_path().write_text(kept, encoding="utf-8")


def _in_profile(profile: Path, needles: dict[str, str]) -> list[str]:
    """Where in Chrome's profile each value stands, as UTF-8 and as the UTF-16 its session files keep what is typed into
    a form in."""
    forms: dict[str, tuple[bytes, bytes]] = {
        name: (text.encode(), text.encode("utf-16-le")) for name, text in needles.items()
    }
    found: list[str] = []
    for path in sorted(profile.rglob("*")):
        try:
            if not path.is_file():
                continue
            data: bytes = path.read_bytes()
        except OSError:
            continue  # a file that Chrome removed while it was read
        for name, (text, wide) in forms.items():
            if wide in data or text in data:
                found.append(f"{name} in {path.relative_to(profile)}")
    return found


async def test_nothing_a_run_filled_is_left_in_chromes_profile_once_chrome_is_closed(
    parts: tuple[Runtime, MemorySecretStore, Path], sites: _Sites
) -> None:
    """Chrome keeps what is typed into a form in the files of its profile: in the session it restores, in the entries it
    makes of a form that was sent. A field Dot filled says "off", and nothing of it is there once Chrome is closed,
    whether the tab stays, sends the form or leaves. The control, a value typed into a plain field without Dot, is found
    there."""
    runtime, _, _ = parts
    vault: VaultItems = await _vault(runtime)
    base: str = f"https://127.0.0.1:{sites.shop.port}"
    needles: dict[str, str] = {}
    for index, leaving in enumerate(("stay", "send", "leave")):
        secret: str = f"Pr0be-Passw0rd-{index}-Zq9"
        login: str = await _a_login(vault, sites, secret)
        ctx, gate = await _context(runtime, "yolo")
        refs: dict[str, str] = await _refs(ctx, gate, f"{base}/signin", {"user": "Email", "pass": "Password"})
        await _call(
            ctx, gate, "browser_fill_login", {"login": login, "username": refs["user"], "password": refs["pass"]}
        )
        seen: ToolResult = await _call(ctx, gate, "browser_snapshot", {})
        if leaving == "send":
            await _call(
                ctx, gate, "browser_act", {"steps": [{"action": "click", "ref": _button(seen.text, "Sign in")}]}
            )
        elif leaving == "leave":
            await _call(ctx, gate, "browser_open", {"url": f"{base}/other"})
        needles[f"password {index}"] = secret
    profile: Path = runtime.browser.profile_dir
    assert runtime.browser._filled_profiles == {profile}, "the profile a login was filled in is the one to clean"
    await runtime.browser.let_go_of_chrome()
    assert await asyncio.to_thread(_in_profile, profile, needles) == []
    assert not (profile / "Default" / "Sessions").exists(), "the session files of the run were deleted"
    assert runtime.browser._filled_profiles == set()
    # The control: in a run of Chrome that was filled nothing, a value typed into a form without Dot's help, which is
    # sent, is in the profile when Chrome closes (Dot deletes the session files only after it filled something), so that
    # the search is one that finds.
    ctx, gate = await _context(runtime, "yolo")
    await _refs(ctx, gate, f"{base}/signin", {"user": "Email"})
    await _page(runtime, ctx.thread_id).locator("[name=user]").fill("control-7r4v")
    seen = await _call(ctx, gate, "browser_snapshot", {})
    await _call(ctx, gate, "browser_act", {"steps": [{"action": "click", "ref": _button(seen.text, "Sign in")}]})
    await runtime.browser.let_go_of_chrome()
    found: list[str] = await asyncio.to_thread(_in_profile, profile, {"control": "control-7r4v"})
    assert found, "the search finds a value that was sent without Dot"
    assert (profile / "Default" / "Sessions").exists(), "a run that filled nothing keeps the session Chrome wrote"


def _holding(home: Path, needles: Sequence[bytes]) -> list[str]:
    """The files of the home of Dot (its database, its log, Chrome's own profile) that hold one of ``needles``."""
    found: list[str] = []
    for path in sorted(home.rglob("*")):
        try:
            data: bytes = path.read_bytes() if path.is_file() else b""
        except OSError:
            continue  # a file that Chrome removed while it was read
        found.extend(f"{needle[:4]!r}… in {path.relative_to(home)}" for needle in needles if needle in data)
    return found


class _Reading(ScriptedProvider):
    """A scripted model that reads the chat it is sent and keeps it. Each of its answers is made from what it was sent
    (``makers`` take the text of the chat so far): a model finds the refs of a page's fields in what it was shown, which
    a script cannot know beforehand."""

    def __init__(self, makers: Sequence[Callable[[str], ScriptedTurn]]) -> None:
        super().__init__(_PROVIDER, ())
        self._makers: tuple[Callable[[str], ScriptedTurn], ...] = tuple(makers)
        self.requests: list[ChatRequest] = []

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.requests.append(request)
        shown: str = "\n".join(message.content for message in request.messages)
        self._turns = (*self._turns, self._makers[len(self.requests) - 1](shown))
        return await super().complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return (await super().model_info(model)).model_copy(update={"vision": True})


def _ref_of(shown: str, label: str) -> str:
    """The ref of the field called ``label`` in the latest view of a page that ``shown`` holds."""
    found: list[str] = re.findall(rf'"{re.escape(label)}"[^\n]*?\[ref=([^\]]+)\]', shown)
    assert found, (label, shown[-600:])
    return found[-1]


def _turn(tool: str, **arguments: object) -> ScriptedTurn:
    return ScriptedTurn(text="", calls=(ScriptedCall(tool=tool, arguments=arguments),))  # type: ignore[arg-type]


def _words(message: Message) -> str:
    """A message of the chat as text, its pictures left out (a picture is looked at, not searched)."""
    return message.model_copy(update={"images": ()}).model_dump_json()


async def test_a_run_of_the_loop_fills_a_login_after_a_yes_and_the_model_reads_no_value_of_it(
    parts: tuple[Runtime, MemorySecretStore, Path], sites: _Sites
) -> None:
    """A run as the daemon drives it, its model a script that keeps what it is sent: it opens the page, asks for the
    login to be filled, and looks at the page. The owner is asked. Nothing of the password is in what the model read at
    any step, in the events, the approval and the messages of the run, nor in the files of the home."""
    runtime, _, home = parts
    vault: VaultItems = await _vault(runtime)
    login: str = await _a_login(vault, sites)
    base: str = f"https://127.0.0.1:{sites.shop.port}"
    model: _Reading = _Reading(
        [
            lambda shown: _turn("browser_open", url=f"{base}/signin"),
            lambda shown: _turn(
                "browser_fill_login",
                login=login,
                username=_ref_of(shown, "Email"),
                password=_ref_of(shown, "Password"),
            ),
            lambda shown: _turn("browser_snapshot"),
            lambda shown: _turn(
                FINISH_TOOL, answer="The login is in the form; the owner presses Sign in.", success=True
            ),
        ]
    )
    await runtime.router.register(_PROVIDER, model)
    manager: RunManager = RunManager(runtime)
    try:
        run: RunRecord = await manager.start(
            "Sign in.", "task", SCRIPTED_MODEL, None, UI_SOURCE, None, None, "standard", None, "ask", False, ()
        )
        asked: ApprovalRecord = await _asked(runtime, run.id)
        assert asked.tool == "browser_fill_login" and "vault_fill" in asked.args["flags"]  # type: ignore[operator]
        # The run waits: the model was asked twice (it opened the page, and asked for the fill) and no more.
        assert len(model.requests) == 2
        await runtime.approvals.resolve(asked.id, True, UI_SOURCE, "")
        async with asyncio.timeout(_ANSWER_WITHIN_S):
            ended: RunRecord = await manager.wait(run.id)
    finally:
        await manager.shutdown()
    assert ended.status == "done", ended.result
    assert len(model.requests) == 4
    secrets_held: tuple[str, ...] = (PASSWORD,)
    read: str = "\n".join(_words(message) for request in model.requests for message in request.messages)
    for secret in secrets_held:
        assert secret not in read, secret
    assert "Filled the username, password of the login" in read
    assert read.count("[hidden]") >= 2, "the snapshot after the fill hides the two fields"
    events: str = "\n".join(event.model_dump_json() for event in await runtime.store.list_events(run.id, 0, 100_000))
    records: list[ApprovalRecord] = [
        record for record in await runtime.store.list_approvals(None, 100) if record.run_id == run.id
    ]
    assert [(record.tool, record.status) for record in records] == [("browser_fill_login", "approved")]
    kept: str = "\n".join(
        [events, *(record.model_dump_json() for record in records)]
        + [_words(message) for message in await runtime.store.load_messages(ended.thread_id, 5)]
    )
    for secret in secrets_held:
        assert secret not in kept, secret
    async with runtime.store._db.execute("PRAGMA wal_checkpoint(TRUNCATE)") as cursor:
        await cursor.fetchall()
    assert await asyncio.to_thread(_holding, home, [PASSWORD.encode()]) == []


async def test_no_event_approval_message_log_line_or_file_of_the_runs_holds_the_password(
    parts: tuple[Runtime, MemorySecretStore, Path], sites: _Sites
) -> None:
    """After the fills above, and one more, look everywhere a run leaves a trace for what it must not hold."""
    runtime, _, home = parts
    ctx, gate, _, _ = await _signed(runtime, sites)
    for tool in ("browser_snapshot", "browser_screenshot"):
        again: ToolResult = await _call(ctx, gate, tool, {})
        assert PASSWORD not in again.text
    # What the database holds is on disk, in the file and in the log beside it, and Chrome's profile, once Chrome is
    # closed, with the form values its session files keep as UTF-16.
    async with runtime.store._db.execute("PRAGMA wal_checkpoint(TRUNCATE)") as cursor:
        await cursor.fetchall()
    await runtime.browser.let_go_of_chrome()
    needles: list[bytes] = [PASSWORD.encode(), PASSWORD.encode("utf-16-le"), json.dumps(PASSWORD).encode()]
    assert await asyncio.to_thread(_holding, home, needles) == []
    assert await asyncio.to_thread(_holding, home, [b"vault_item_kept"]), "the log is where it was looked for"
