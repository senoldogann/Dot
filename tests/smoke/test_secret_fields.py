"""A field that takes a secret, through the browser tools and the gate: in ask mode the step is refused, and a yolo run
hands the field to the user (item 429), whose answer the gate keeps.

The fixture page asks for a code from an e-mail in a field marked ``autocomplete="one-time-code"`` whose label names
nothing secret, so only the field's own marking, read when the step runs, tells what it takes. The check is what reached
the fixture server. A real headless Chrome is used; no model is called.
"""

import asyncio
import re
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.gate import RunGate, SiteLocks
from dotagent.agent.review import ReviewRequest, ReviewVerdict
from dotagent.agent.verify import new_facts
from dotagent.browser.types import SecretField, SecretFieldCheck
from dotagent.config import load_config
from dotagent.core.paths import workspace_dir
from dotagent.core.types import JsonObject
from dotagent.policy.types import ApprovalMode
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import ApprovalRecord, RunRecord, ThreadRecord
from dotagent.tools.base import PreparedCall, ToolResult
from dotagent.tools.browser import _secret_field_check
from dotagent.tools.context import ToolContext

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]
# How long the test waits for the hand-off's card and for the step once it is answered.
_ANSWER_WITHIN_S: float = 30.0
# How often the test looks for the form's submission: the browser's Enter goes out of the page's own event loop.
_POLL_S: float = 0.05

GOAL: str = "Confirm the sign-up with the code from the e-mail."
CODE: str = "482913"
_PAGES: dict[str, str] = {
    "/verify": """<!doctype html><title>Verify</title><h1>Check your e-mail</h1>
<form action="/verified" method="get">
<label>Code from the e-mail <input name="code" autocomplete="one-time-code"></label>
<button type="submit">Confirm</button>
</form>""",
    "/verified": "<!doctype html><title>Verified</title><h1>Verified</h1>",
    # A note editor that hands the focus to the code field a moment after it takes it.
    "/verify-note": """<!doctype html><title>Verify</title><h1>Check your e-mail</h1>
<div id="note" contenteditable="true" role="textbox" aria-label="Note"></div>
<label>Code from the e-mail <input id="code" name="code" autocomplete="one-time-code"></label>
<script>
const code = document.getElementById('code');
document.getElementById('note').addEventListener('focus', () => setTimeout(() => code.focus(), 20));
</script>""",
    # The label stands apart from its field, so it has a ref of its own.
    "/verify-apart": """<!doctype html><title>Verify</title><h1>Check your e-mail</h1>
<form action="/verified" method="get">
<label for="code" style="display: block">Code from the e-mail</label>
<input id="code" name="code" autocomplete="one-time-code">
<button type="submit">Confirm</button>
</form>""",
}


class _Site(ThreadingHTTPServer):
    """A local web server with the code form; it keeps the address of every submission it receives."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.submissions: list[str] = []


class _Handler(BaseHTTPRequestHandler):
    server: _Site

    def do_GET(self) -> None:
        path: str = self.path.split("?", 1)[0]
        if path == "/verified":
            self.server.submissions.append(self.path)
        body: bytes = _PAGES.get(path, "not found").encode()
        self.send_response(200 if path in _PAGES else 404)
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


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text("[browser]\nheadless = true\n", encoding="utf-8")
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


async def _nobody(request: ReviewRequest) -> ReviewVerdict:
    raise AssertionError(f"the review model must not be asked about this: {request.action.summary}")


async def _context(runtime: Runtime, approvals: ApprovalMode, goal: str = GOAL) -> tuple[ToolContext, RunGate]:
    """A run of ``approvals`` with ``goal`` in the store, its gate and the context its tools get."""
    thread: ThreadRecord = await runtime.store.create_thread("secret fields", "test")
    run: RunRecord = await runtime.store.create_run(
        thread.id,
        goal,
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
        (goal,),
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
        user_text=lambda: "\n".join(gate.instructions),
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


async def _code_field(ctx: ToolContext, gate: RunGate, site: _Site) -> str:
    opened: ToolResult = await _call(
        ctx, gate, "browser_open", {"url": f"http://127.0.0.1:{site.server_address[1]}/verify"}
    )
    match: re.Match[str] | None = re.search(r'textbox "Code from the e-mail"[^\n]*?\[ref=([^\]]+)\]', opened.text)
    assert match is not None, opened.text
    return match.group(1)


async def _apart_refs(ctx: ToolContext, gate: RunGate, site: _Site) -> dict[str, str]:
    """The refs of the heading, the label and the code field of the page whose label stands apart."""
    opened: ToolResult = await _call(
        ctx, gate, "browser_open", {"url": f"http://127.0.0.1:{site.server_address[1]}/verify-apart"}
    )
    patterns: dict[str, str] = {
        "heading": r'heading "Check your e-mail"[^\n]*?\[ref=([^\]]+)\]',
        "label": r"generic \[ref=([^\]]+)\]: Code from the e-mail",
        "field": r'textbox "Code from the e-mail"[^\n]*?\[ref=([^\]]+)\]',
    }
    refs: dict[str, str] = {}
    for name, pattern in patterns.items():
        match: re.Match[str] | None = re.search(pattern, opened.text)
        assert match is not None, (name, opened.text)
        refs[name] = match.group(1)
    return refs


async def _records(ctx: ToolContext) -> list[ApprovalRecord]:
    return [record for record in await ctx.runtime.store.list_approvals(None, 50) if record.run_id == ctx.run_id]


async def test_ask_mode_refuses_to_type_a_one_time_code(runtime: Runtime, site: _Site) -> None:
    ctx, gate = await _context(runtime, "ask")
    field: str = await _code_field(ctx, gate, site)
    acted: ToolResult = await _call(
        ctx, gate, "browser_act", {"steps": [{"action": "fill", "ref": field, "text": CODE}]}
    )
    assert "refusing to type into a one-time-code field" in acted.text
    assert site.submissions == []
    assert await _records(ctx) == []


async def test_ask_mode_types_a_one_time_code_the_user_gave(runtime: Runtime, site: _Site) -> None:
    """Item 797: the code is the user's to hand over; a run in ask mode types it and submits the form."""
    goal: str = f"Confirm the sign-up with the code {CODE} from the e-mail."
    ctx, gate = await _context(runtime, "ask", goal=goal)
    field: str = await _code_field(ctx, gate, site)
    acted: ToolResult = await _call(
        ctx,
        gate,
        "browser_act",
        {"steps": [{"action": "fill", "ref": field, "text": CODE}, {"action": "press", "ref": field, "key": "Enter"}]},
    )
    assert "refusing to type into" not in acted.text, acted.text
    assert any(CODE in submission for submission in site.submissions), site.submissions
    assert await _records(ctx) == []


async def test_ask_mode_refuses_a_code_one_key_at_a_time_even_when_the_user_gave_it(
    runtime: Runtime, site: _Site
) -> None:
    """Item 797: a key is not the whole secret; the code goes in as a value, not a character at a time."""
    goal: str = f"Confirm the sign-up with the code {CODE} from the e-mail."
    ctx, gate = await _context(runtime, "ask", goal=goal)
    field: str = await _code_field(ctx, gate, site)
    acted: ToolResult = await _call(
        ctx, gate, "browser_act", {"steps": [{"action": "press", "ref": field, "key": "4"}]}
    )
    assert "refusing to type into a one-time-code field" in acted.text


@pytest.mark.parametrize(
    "steps",
    [
        # fill writes into the control of the label it is given.
        [{"action": "fill", "ref": "label", "text": CODE}],
        # The keys of type and press go to the field that has the focus, whatever element their ref names.
        [{"action": "click", "ref": "field"}, {"action": "type", "ref": "heading", "text": CODE}],
        [{"action": "click", "ref": "field"}, {"action": "insert", "ref": "heading", "text": CODE}],
        [{"action": "click", "ref": "field"}, {"action": "press", "ref": "heading", "key": "Digit4"}],
        [{"action": "click", "ref": "field"}, {"action": "press", "key": "4"}],
        # A drop puts what it carries into the field it drops onto.
        [{"action": "drag", "ref": "heading", "to_ref": "field"}],
    ],
    ids=[
        "fill-the-label",
        "type-at-the-heading",
        "insert-at-the-heading",
        "press-at-the-heading",
        "press-without-a-ref",
        "drag-onto-the-field",
    ],
)
async def test_ask_mode_refuses_the_code_whatever_element_the_step_names(
    runtime: Runtime, site: _Site, steps: list[dict[str, str]]
) -> None:
    ctx, gate = await _context(runtime, "ask")
    refs: dict[str, str] = await _apart_refs(ctx, gate, site)
    named: list[JsonObject] = [
        {name: refs[value] if name in ("ref", "to_ref") else value for name, value in step.items()} for step in steps
    ]
    acted: ToolResult = await _call(ctx, gate, "browser_act", {"steps": named})
    assert "refusing to type into a one-time-code field" in acted.text
    assert await _records(ctx) == []


async def test_ask_mode_refuses_text_for_an_editor_once_the_focus_went_to_the_code_field(
    runtime: Runtime, site: _Site
) -> None:
    ctx, gate = await _context(runtime, "ask")
    opened: ToolResult = await _call(
        ctx, gate, "browser_open", {"url": f"http://127.0.0.1:{site.server_address[1]}/verify-note"}
    )
    note: re.Match[str] | None = re.search(r'textbox "Note"[^\n]*?\[ref=([^\]]+)\]', opened.text)
    assert note is not None, opened.text
    acted: ToolResult = await _call(
        ctx, gate, "browser_act", {"steps": [{"action": "insert", "ref": note.group(1), "text": CODE}]}
    )
    assert "refusing to type into a one-time-code field" in acted.text
    assert await _records(ctx) == []


async def test_a_yolo_run_types_the_code_field_itself_and_the_record_says_why(runtime: Runtime, site: _Site) -> None:
    """A yolo run asks nothing (item 443): the code field is the run's to fill — the form is submitted with the code —
    and the record of the step keeps the reason it would have asked for."""
    ctx, gate = await _context(runtime, "yolo")
    field: str = await _code_field(ctx, gate, site)
    steps: list[JsonObject] = [{"action": "fill", "ref": field, "text": CODE}, {"action": "press", "key": "Enter"}]
    acted: ToolResult = await _call(ctx, gate, "browser_act", {"steps": steps})
    assert "refusing" not in acted.text and "the user handled" not in acted.text, acted.text
    for _ in range(40):
        if site.submissions:
            break
        await asyncio.sleep(_POLL_S)
    assert site.submissions and CODE in site.submissions[0], site.submissions
    records: list[ApprovalRecord] = await _records(ctx)
    kept = [(record.status, record.decided_by, record.severity) for record in records]
    assert kept == [("approved", "yolo", "high")], kept
    assert records[0].summary.startswith(f"Type into a one-time-code field ({field})")
    assert records[0].note == f"credential actions are handed to the user: {records[0].summary}"


async def test_a_yolo_run_types_a_card_field_itself_whatever_its_batch_was_allowed(runtime: Runtime) -> None:
    """A card field is asked of the gate on its own, a batch's yes never covering it (item 429); in yolo the run types
    it (item 443) and the record keeps the reason."""
    ctx, _ = await _context(runtime, "yolo")
    check: SecretFieldCheck = _secret_field_check(ctx)
    field: SecretField = SecretField(ref="e7", kind="cc-number", url="https://shop.example/checkout")
    await check(field, None)
    records: list[ApprovalRecord] = await _records(ctx)
    assert [(record.decided_by, record.args["flags"]) for record in records] == [("yolo", ["financial"])]
