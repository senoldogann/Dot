"""A login of the owner's 1Password filled into a page (item 493), through the browser tools and the gate, with a
stand-in for ``op`` and a real headless Chrome; no model is called. A yolo run fills it and no result shows a value:
not the fill's, not the view of the filled form. Scripts stay off in the chat's tabs while the filled tab is open, and
run again once it is closed. A login of another site is refused before anything is asked, a password goes into a
password field alone, a frame of another site gets nothing, nor does a form that sends what is typed in its address,
and a page a script ran in gets no login. In auto the owner answers the fill, not the review model.
"""

import asyncio
import io
import json
import re
import secrets
import stat
import sys
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, quote_plus

import pytest
import pytest_asyncio
from PIL import Image
from playwright.async_api import Page

from dotagent.agent.gate import RunGate, SiteLocks
from dotagent.agent.review import ReviewRequest, ReviewVerdict
from dotagent.agent.verify import new_facts
from dotagent.browser.types import Screenshot
from dotagent.config import load_config
from dotagent.core.errors import BrowserError, ToolInputError
from dotagent.core.paths import env_file_path, workspace_dir
from dotagent.core.types import JsonObject
from dotagent.policy.types import ApprovalMode
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.secrets import write_env_value
from dotagent.server import UI_SOURCE
from dotagent.store.models import ApprovalRecord, RunRecord, ThreadRecord
from dotagent.tools.base import PreparedCall, ToolResult
from dotagent.tools.context import ToolContext
from dotagent.vault.onepassword import TOKEN_NAME

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

STAND_IN: Path = Path(__file__).resolve().parent.parent / "fixtures" / "op_stand_in.py"
TOKEN: str = "ops_" + secrets.token_urlsafe(32)
USERNAME: str = "ada@example.com"
PASSWORD: str = "S3cret-Passw0rd!"
CODE: str = "482913"
_ANSWER_WITHIN_S: float = 30.0
_HOME: str = "<!doctype html><title>Home</title><h1>Welcome</h1>"
_POLL_S: float = 0.05


class _Site(ThreadingHTTPServer):
    """The login form, its frame of another host (``localhost``, where the page is ``127.0.0.1``), whose script makes
    its field say it is in the login's page, two forms sent by GET (one names the method, one names none), and the
    page the forms go to, which keeps what each POST sent and the address of each GET that reached it."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.submissions: list[str] = []
        self.addresses: list[str] = []

    @property
    def port(self) -> int:
        return int(self.server_address[1])


class _Handler(BaseHTTPRequestHandler):
    server: _Site

    def do_GET(self) -> None:
        path: str = self.path.split("?", 1)[0]
        port: int = self.server.port
        pages: dict[str, str] = {
            "/login": f"""<!doctype html><title>Sign in</title><h1>Sign in</h1>
<style>[name=user] {{ position: fixed; left: 400px; top: 300px; width: 200px; height: 60px }}</style>
<form action="/home" method="post">
<label>Email <input name="user" autocomplete="username"></label>
<label>Password <input name="pass" type="password" autocomplete="current-password"></label>
<label>Code <input name="code" autocomplete="one-time-code"></label>
<button type="submit">Sign in</button>
</form>
<iframe title="An advert" src="http://localhost:{port}/frame"></iframe>""",
            "/frame": f"""<!doctype html><title>Advert</title>
<label>Frame password <input name="pass" type="password"></label>
<script>
const login = {{ location: {{ href: "http://127.0.0.1:{port}/login" }} }};
Object.defineProperty(document.querySelector("input"), "ownerDocument", {{ get: () => login }});
</script>""",
            "/get-login": """<!doctype html><title>Search sign in</title>
<form action="/home" method="get"><label>Password <input name="pass" type="password"></label></form>""",
            "/plain-login": """<!doctype html><title>Plain sign in</title>
<form action="/home"><label>Password <input name="pass" type="password"></label></form>""",
            "/script-login": """<!doctype html><title>Script sign in</title>
<label>Password <input name="pass" type="password"></label>
<button type="button" onclick="fetch('/home?p=' + encodeURIComponent(document.querySelector('input').value))
  .then(() => { document.title = 'sent'; }, () => { document.title = 'refused'; })">Sign in</button>""",
            "/address-login": """<!doctype html><title>Address sign in</title>
<label>Password <input name="pass" type="password"></label>
<button type="button" onclick="location.href = '/home?p=' + encodeURIComponent(document.querySelector('input').value)">
Sign in</button>""",
            "/editable-login": """<!doctype html><title>Editable sign in</title><h1>Editable sign in</h1>
<div id="code" contenteditable="true" role="textbox" aria-label="Code"></div>
<label>Password <input name="pass" type="password" autocomplete="current-password"></label>""",
            "/home": _HOME,
        }
        if path == "/home" and "?" in self.path:
            self.server.addresses.append(self.path)
        body: bytes = pages.get(path, "not found").encode()
        self.send_response(200 if path in pages else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        """The form of ``/login``: its fields as sent, kept, and the home page."""
        self.server.submissions.append(self.rfile.read(int(self.headers.get("Content-Length", "0"))).decode())
        body: bytes = _HOME.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def site() -> Iterator[_Site]:
    server: _Site = _Site()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _stand_in(folder: Path, port: int) -> None:
    """An ``op`` in ``folder`` that the stand-in answers, from a state of one vault shared with the service account
    whose token is :data:`TOKEN`: a login of the site served here and one of another site."""
    state: JsonObject = {
        "accounts": [{"url": "my.1password.com", "email": "ada@example.com"}],
        "vaults": [{"id": "v1", "name": "Shared with Dot"}],
        "token": TOKEN,
        "reads": ["v1"],
        "made_token": TOKEN,
        "items": {
            "v1": [
                {
                    "id": "i1",
                    "title": "This site",
                    "urls": [{"href": f"http://127.0.0.1:{port}/login"}],
                    "fields": [
                        {"id": "username", "type": "STRING", "purpose": "USERNAME", "value": USERNAME},
                        {"id": "password", "type": "CONCEALED", "purpose": "PASSWORD", "value": PASSWORD},
                    ],
                    "otp": CODE,
                },
                {
                    "id": "i2",
                    "title": "Another site",
                    "urls": [{"href": "https://example.org/login"}],
                    "fields": [{"id": "password", "type": "CONCEALED", "purpose": "PASSWORD", "value": "x" * 12}],
                },
            ]
        },
    }
    (folder / "state.json").write_text(json.dumps(state), encoding="utf-8")
    wrapper: Path = folder / "op"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{STAND_IN}" "{folder / "state.json"}" "$@"\n')
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory, site: _Site) -> AsyncIterator[Runtime]:
    """A runtime whose browser has no window, whose PATH finds the stand-in ``op``, with the token in its .env."""
    home: Path = tmp_path_factory.mktemp("home")
    tools: Path = tmp_path_factory.mktemp("op")
    _stand_in(tools, site.port)
    (home / "config.toml").write_text("[browser]\nheadless = true\n", encoding="utf-8")
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    patch.setenv("PATH", f"{tools}:/usr/bin:/bin")
    write_env_value(env_file_path(), TOKEN_NAME, TOKEN)
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


async def _nobody(request: ReviewRequest) -> ReviewVerdict:
    raise AssertionError(f"the review model must not be asked about this: {request.action.summary}")


async def _context(runtime: Runtime, approvals: ApprovalMode) -> tuple[ToolContext, RunGate]:
    """A run of ``approvals`` in the store, its gate and the context its tools get."""
    thread: ThreadRecord = await runtime.store.create_thread("vault fill", "test")
    run: RunRecord = await runtime.store.create_run(
        thread.id,
        "Sign in to the site.",
        "task",
        "ollama:none",
        None,
        "test",
        "medium",
        "standard",
        None,
        approvals,
        False,
        None,
        None,
        None,
    )
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
        ("Sign in to the site.",),
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
    """A call as the executor makes it: classified, authorised, then run."""
    prepared: PreparedCall = await ctx.runtime.registry.tools[name].prepare(arguments, ctx)
    await gate.authorize(prepared.action)
    return await prepared.run(ctx)


async def _form(ctx: ToolContext, gate: RunGate, site: _Site) -> dict[str, str]:
    """The refs of the form's fields and of the frame's password field, as the page opened shows them."""
    opened: ToolResult = await _call(ctx, gate, "browser_open", {"url": f"http://127.0.0.1:{site.port}/login"})
    refs: dict[str, str] = {}
    for name, label in (("user", "Email"), ("pass", "Password"), ("code", "Code"), ("frame", "Frame password")):
        match: re.Match[str] | None = re.search(rf'"{label}"[^\n]*?\[ref=([^\]]+)\]', opened.text)
        assert match is not None, (name, opened.text)
        refs[name] = match.group(1)
    return refs


def _addressed(site: _Site) -> bool:
    """Whether a GET that reached the site had the password in its address, as it is or escaped."""
    return any(PASSWORD in address or quote_plus(PASSWORD) in address for address in site.addresses)


def _unseen(*texts: str) -> None:
    """No value is in ``texts``, as typed or as an address writes it (a form sent by GET puts it there)."""
    for text in texts:
        for value in (PASSWORD, CODE, USERNAME):
            assert value not in text and quote(value, safe="") not in text and quote_plus(value) not in text, text


async def test_a_yolo_run_fills_the_login_and_no_result_shows_a_value(runtime: Runtime, site: _Site) -> None:
    ctx, gate = await _context(runtime, "yolo")
    refs: dict[str, str] = await _form(ctx, gate, site)
    listed: ToolResult = await _call(ctx, gate, "browser_logins", {})
    assert "i1: This site (127.0.0.1)" in listed.text and "i2: Another site (example.org)" in listed.text
    _unseen(listed.text)
    filled: ToolResult = await _call(
        ctx,
        gate,
        "browser_fill_login",
        {"login": "i1", "username": refs["user"], "password": refs["pass"], "otp": refs["code"]},
    )
    assert "Filled the username, password, otp of the login This site" in filled.text, filled.text
    _unseen(filled.text)
    # A screenshot covers the filled fields in every mode, yolo too: the username shows in its field.
    shot: Screenshot = await runtime.browser.screenshot(ctx.thread_id, False)
    with Image.open(io.BytesIO(shot.data)) as picture:
        pixel: object = picture.convert("RGB").getpixel((500, 330))
    assert isinstance(pixel, tuple) and all(abs(channel - 0x33) < 24 for channel in pixel), pixel
    # The view of the form hides the value of every field Dot filled, in yolo too (item 621).
    seen: ToolResult = await _call(ctx, gate, "browser_snapshot", {})
    _unseen(seen.text)
    assert seen.text.count("[hidden]") == 3, seen.text
    # No script runs in the chat's tabs while the filled tab is open.
    with pytest.raises(BrowserError, match="scripts stay off"):
        await _call(ctx, gate, "browser_eval", {"script": "document.querySelector('input[type=password]').value"})
    # The form goes with the values, and the page it lands on shows none of them.
    sent: ToolResult = await _call(
        ctx, gate, "browser_act", {"steps": [{"action": "press", "ref": refs["code"], "key": "Enter"}]}
    )
    _unseen(sent.text)
    assert site.submissions and quote_plus(PASSWORD) in site.submissions[-1], site.submissions
    # Another page of the same tab: still no script, since going back could bring the filled page back whole.
    with pytest.raises(BrowserError, match="scripts stay off"):
        await _call(ctx, gate, "browser_eval", {"script": "location.href"})
    # Closed, the tab holds them no more: the new one runs scripts again.
    await _call(ctx, gate, "browser_new_tab", {"url": f"http://127.0.0.1:{site.port}/home"})
    await _call(ctx, gate, "browser_close_tab", {"index": 1})
    read: ToolResult = await _call(ctx, gate, "browser_eval", {"script": "document.title"})
    assert "Home" in read.text, read.text
    # The values stay the chat's to mask when its tabs are let go of, as an idle chat's are, while the chat goes on.
    await runtime.browser.release_thread(ctx.thread_id)
    assert PASSWORD in runtime.browser.vault_values(ctx.thread_id)
    # The yolo run's record of the fill says what was filled, never a value.
    records: list[ApprovalRecord] = [
        record for record in await runtime.store.list_approvals(None, 50) if record.run_id == ctx.run_id
    ]
    assert [record.tool for record in records] == ["browser_fill_login"], records
    _unseen(*(record.model_dump_json() for record in records))


async def test_a_login_of_another_site_a_password_in_a_text_field_and_a_frame_get_nothing(
    runtime: Runtime, site: _Site
) -> None:
    ctx, gate = await _context(runtime, "yolo")
    refs: dict[str, str] = await _form(ctx, gate, site)
    with pytest.raises(ToolInputError, match="fills a login only on its own site"):
        await _call(ctx, gate, "browser_fill_login", {"login": "i2", "password": refs["pass"]})
    with pytest.raises(BrowserError, match="a password goes only into a password field"):
        await _call(ctx, gate, "browser_fill_login", {"login": "i1", "password": refs["user"]})
    with pytest.raises(BrowserError, match="in a frame of another site"):
        await _call(ctx, gate, "browser_fill_login", {"login": "i1", "password": refs["frame"]})
    # A script of the run ran in the page: it may have left a listener that copies what is typed, in its page or in a
    # tab it opened, so no tab of the chat gets a login while one of them is open. The fill makes them new itself
    # (item 697): every tab of the chat closes, the page opens again in a new tab, and nothing is filled until the
    # run fills again with the refs of that page.
    await _call(ctx, gate, "browser_new_tab", {"url": f"http://127.0.0.1:{site.port}/login"})
    await _call(ctx, gate, "browser_eval", {"script": "1 + 1"})
    # A fill that would be refused on that page anyway closes no tab first.
    with pytest.raises(BrowserError, match="not one of the sites the fill was asked for"):
        await ctx.browser.fresh_for_login(ctx.thread_id, lambda address: False, ["password"])
    assert len((await ctx.browser.snapshot(ctx.thread_id, True)).tabs) == 2
    reset: ToolResult = await _call(ctx, gate, "browser_fill_login", {"login": "i1", "password": refs["pass"]})
    assert reset.text.startswith("Nothing was filled yet"), reset.text
    assert "Tab 1 of 1" in reset.text and "closed them and opened this page again" in reset.text, reset.text
    _unseen(reset.text)
    fresh: re.Match[str] | None = re.search(r'"Password"[^\n]*?\[ref=([^\]]+)\]', reset.text)
    assert fresh is not None, reset.text
    filled: ToolResult = await _call(ctx, gate, "browser_fill_login", {"login": "i1", "password": fresh.group(1)})
    assert filled.text.startswith("Filled the password"), filled.text
    _unseen(filled.text)
    # A form that sends what is typed in the page's address gets no password: the address would hold it.
    shown: ToolResult = await _call(ctx, gate, "browser_open", {"url": f"http://127.0.0.1:{site.port}/get-login"})
    field: re.Match[str] | None = re.search(r'"Password"[^\n]*?\[ref=([^\]]+)\]', shown.text)
    assert field is not None, shown.text
    with pytest.raises(BrowserError, match="its method is GET"):
        await _call(ctx, gate, "browser_fill_login", {"login": "i1", "password": field.group(1)})
    # A form that names no method goes by GET as well, as a browser reads it (item 621): it gets no password either.
    plain: ToolResult = await _call(ctx, gate, "browser_open", {"url": f"http://127.0.0.1:{site.port}/plain-login"})
    bare: re.Match[str] | None = re.search(r'"Password"[^\n]*?\[ref=([^\]]+)\]', plain.text)
    assert bare is not None, plain.text
    with pytest.raises(BrowserError, match="its method is GET, or it names none"):
        await _call(ctx, gate, "browser_fill_login", {"login": "i1", "password": bare.group(1)})
    # A page whose script builds an address from a field that is in no form says nothing of how it is sent: a request
    # of its own that would carry the password is refused by Dot's Chrome and never reaches the site.
    scripted: ToolResult = await _call(ctx, gate, "browser_open", {"url": f"http://127.0.0.1:{site.port}/script-login"})
    loose: re.Match[str] | None = re.search(r'"Password"[^\n]*?\[ref=([^\]]+)\]', scripted.text)
    assert loose is not None, scripted.text
    await _call(ctx, gate, "browser_fill_login", {"login": "i1", "password": loose.group(1)})
    button: re.Match[str] | None = re.search(r'button "Sign in"[^\n]*?\[ref=([^\]]+)\]', scripted.text)
    assert button is not None, scripted.text
    await _call(ctx, gate, "browser_act", {"steps": [{"action": "click", "ref": button.group(1)}]})
    after: ToolResult = await _call(ctx, gate, "browser_snapshot", {})
    assert "title: refused" in after.text, after.text
    assert not _addressed(site), site.addresses
    # The tab's own address is the run's, and a request the run caused is judged by no value (a refusal by it would tell
    # a run that guesses whether it guessed): the page sends the tab to an address that holds the password, and what
    # the run is shown of it is masked.
    moving: ToolResult = await _call(ctx, gate, "browser_open", {"url": f"http://127.0.0.1:{site.port}/address-login"})
    near: re.Match[str] | None = re.search(r'"Password"[^\n]*?\[ref=([^\]]+)\]', moving.text)
    assert near is not None, moving.text
    await _call(ctx, gate, "browser_fill_login", {"login": "i1", "password": near.group(1)})
    submit: re.Match[str] | None = re.search(r'button "Sign in"[^\n]*?\[ref=([^\]]+)\]', moving.text)
    assert submit is not None, moving.text
    went: ToolResult = await _call(ctx, gate, "browser_act", {"steps": [{"action": "click", "ref": submit.group(1)}]})
    assert _addressed(site), "the tab went to the address"
    _unseen(went.text)


async def test_a_one_time_code_and_a_password_go_into_no_element_that_is_edited(runtime: Runtime, site: _Site) -> None:
    """An element that is edited (``contenteditable``) takes typed text, shows it in the text of the page, and has no
    autocomplete to keep Chrome from storing it; a one-time code is too short to be masked as text. It gets neither the
    code nor the password, and nothing is typed or held back."""
    ctx, gate = await _context(runtime, "yolo")
    opened: ToolResult = await _call(ctx, gate, "browser_open", {"url": f"http://127.0.0.1:{site.port}/editable-login"})
    editable: re.Match[str] | None = re.search(r'"Code"[^\n]*?\[ref=([^\]]+)\]', opened.text)
    assert editable is not None, opened.text
    for part in ("otp", "password"):
        with pytest.raises(BrowserError, match="is not a form field") as refused:
            await _call(ctx, gate, "browser_fill_login", {"login": "i1", part: editable.group(1)})
        _unseen(str(refused.value))
    page: Page = runtime.browser._threads[ctx.thread_id].tabs[0].page
    assert await page.locator("#code").inner_text() == ""
    assert not runtime.browser.holds_back(ctx.thread_id)


async def test_in_auto_the_owner_answers_the_fill_and_not_the_review_model(runtime: Runtime, site: _Site) -> None:
    ctx, gate = await _context(runtime, "auto")
    refs: dict[str, str] = await _form(ctx, gate, site)
    filling: asyncio.Task[ToolResult] = asyncio.create_task(
        _call(ctx, gate, "browser_fill_login", {"login": "This site", "password": refs["pass"]})
    )
    asked: ApprovalRecord | None = None
    async with asyncio.timeout(_ANSWER_WITHIN_S):
        while asked is None:
            pending: list[ApprovalRecord] = [
                record
                for record in await runtime.store.list_approvals(None, 50)
                if record.run_id == ctx.run_id and record.status == "pending"
            ]
            asked = pending[0] if pending else None
            await asyncio.sleep(_POLL_S)
    assert "Fill your login This site (127.0.0.1) from 1Password" in asked.summary, asked.summary
    assert "whatever your rules say" in asked.summary
    await runtime.approvals.resolve(asked.id, True, UI_SOURCE, "")
    filled: ToolResult = await filling
    _unseen(filled.text)
