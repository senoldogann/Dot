"""The pace of a site on a real headless Chrome: a repost and a follow are flagged from their labels, public actions are
spaced and stop at the hourly limit with the time of the next one, even in ``yolo``, a follow is counted apart, and a
batch with a repost and a follow is sent back, since the pace of reposts is set; two follows and a like, which no pace
limits here, go in one call and each click lands (item 266). No model is called.
"""

import asyncio
import json
import re
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.gate import RunGate
from dotagent.agent.review import ReviewRequest, ReviewVerdict
from dotagent.agent.verify import new_facts
from dotagent.browser.types import ScriptRun
from dotagent.config import load_config
from dotagent.core.errors import PolicyDenied, ToolInputError
from dotagent.core.paths import workspace_dir
from dotagent.core.types import JsonObject
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import EventRecord, RunRecord, ThreadRecord
from dotagent.tools.base import PreparedCall
from dotagent.tools.context import ToolContext

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]
# How long a test waits for the question a post raises and for the step once it is answered.
_ANSWER_WITHIN_S: float = 60.0

GOAL: str = "Share the post and follow its author."
SPACING_S: float = 1.0
_PAGE: bytes = b"""<!doctype html><title>Timeline</title><h1>Timeline</h1><p>A post worth sharing.</p>
<button type="button">Repost</button>
<button type="button" onclick="clicked(this)">Follow @author</button>
<button type="button" onclick="clicked(this)">Follow @editor</button>
<button type="button" onclick="clicked(this)">Like</button>
<script>
function clicked(button) {
  document.body.dataset.clicks = (document.body.dataset.clicks || "") + button.textContent + ";";
}
</script>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(_PAGE)))
        self.end_headers()
        self.wfile.write(_PAGE)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}/"
    finally:
        server.shutdown()
        server.server_close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f"[browser]\nheadless = true\n\n[policy.pace]\npublic_per_hour = 2\npublic_spacing_s = {SPACING_S}\n",
        encoding="utf-8",
    )
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


async def _yolo_context(runtime: Runtime) -> ToolContext:
    thread: ThreadRecord = await runtime.store.create_thread("pace", "test")
    run: RunRecord = await runtime.store.create_run(
        thread.id,
        GOAL,
        "task",
        "ollama:none",
        None,
        "test",
        "medium",
        "standard",
        None,
        "yolo",
        False,
        None,
        None,
        None,
    )
    return ToolContext(
        runtime=runtime,
        browser=runtime.browser,
        run_id=run.id,
        agent_id=None,
        thread_id=thread.id,
        mode="task",
        approvals="yolo",
        questions="leave",
        workspace=workspace_dir(),
        gate=RunGate(
            runtime.store,
            runtime.bus,
            runtime.approvals,
            run.id,
            "task",
            "aggressive",
            runtime.config.policy.pace,
            runtime.config.policy.spend,
            "yolo",
            _nobody,
            (GOAL,),
            runtime.site_locks,
        ),
        cancel=asyncio.Event(),
        output_chars=runtime.config.agent.tool_output_chars,
        facts=new_facts,
    )


def _ref(snapshot: str, name: str) -> str:
    match: re.Match[str] | None = re.search(rf'button "{name}"[^\n]*?\[ref=([^\]]+)\]', snapshot)
    assert match is not None, (name, snapshot)
    return match.group(1)


async def _act(ctx: ToolContext, steps: list[JsonObject]) -> PreparedCall:
    """``browser_act`` through the gate, as the executor runs it."""
    prepared: PreparedCall = await ctx.runtime.registry.tools["browser_act"].prepare({"steps": steps}, ctx)
    await ctx.gate.authorize(prepared.action)
    await prepared.run(ctx)
    return prepared


async def _answered(ctx: ToolContext, steps: list[JsonObject]) -> PreparedCall:
    """``browser_act`` through the gate: in yolo the post in the user's name is the run's to make, and nothing is asked
    (item 443) — only the site's pace has a word on it."""
    return await _act(ctx, steps)


async def test_public_actions_are_spaced_and_stop_at_the_limit_in_yolo_and_a_follow_is_counted_apart(
    runtime: Runtime, site: str
) -> None:
    ctx: ToolContext = await _yolo_context(runtime)
    opened: PreparedCall = await runtime.registry.tools["browser_open"].prepare({"url": site}, ctx)
    page: str = (await opened.run(ctx)).text
    repost: JsonObject = {"action": "click", "ref": _ref(page, "Repost")}
    follow: JsonObject = {"action": "click", "ref": _ref(page, "Follow @author")}
    with pytest.raises(ToolInputError, match="one public or social action at most"):
        await runtime.registry.tools["browser_act"].prepare({"steps": [repost, follow]}, ctx)
    first: PreparedCall = await _answered(ctx, [repost])
    await _answered(ctx, [repost])
    public: list[float] = await runtime.store.paced_times("127.0.0.1", "public", 0.0)
    with pytest.raises(PolicyDenied) as refused:
        await ctx.gate.authorize(first.action)
    followed: PreparedCall = await _act(ctx, [follow])
    events: list[EventRecord] = await runtime.store.events_of_kind(ctx.run_id, "policy", 10)
    assert (first.action.risk, first.action.flags) == ("medium", ("public_post",))
    # The second repost was let through a spacing after the first; the event loop may wake a hair early.
    assert len(public) == 2 and public[1] - public[0] >= SPACING_S - 0.01, public
    assert refused.value.message.startswith("Pace for 127.0.0.1: 2 of 2 public actions in the last hour"), refused.value
    # A yolo run asks nothing (item 443), so the pace alone refuses the third.
    assert [(event.data["verdict"], event.data["source"]) for event in events] == [("deny", "pace")]
    assert (followed.action.risk, followed.action.flags) == ("low", ("social",))
    assert await runtime.store.paced_times("127.0.0.1", "social", 0.0) != []


async def test_a_batch_carries_several_social_actions_while_no_social_pace_is_set(runtime: Runtime, site: str) -> None:
    """Item 266: the pace here limits public actions and leaves social ones free, so two follows and a like go in one
    call, each click lands, and the gate counts the call once."""
    ctx: ToolContext = await _yolo_context(runtime)
    opened: PreparedCall = await runtime.registry.tools["browser_open"].prepare({"url": site}, ctx)
    page: str = (await opened.run(ctx)).text
    steps: list[JsonObject] = [
        {"action": "click", "ref": _ref(page, name)} for name in ("Follow @author", "Follow @editor", "Like")
    ]
    before: int = len(await runtime.store.paced_times("127.0.0.1", "social", 0.0))
    batch: PreparedCall = await _act(ctx, steps)
    assert (batch.action.risk, batch.action.flags) == ("low", ("social",))
    clicks: ScriptRun = await ctx.browser.run_script(ctx.thread_id, "document.body.dataset.clicks", 5.0, False)
    assert json.loads(clicks.text) == "Follow @author;Follow @editor;Like;"
    assert len(await runtime.store.paced_times("127.0.0.1", "social", 0.0)) == before + 1
