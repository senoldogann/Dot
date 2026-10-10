"""The typed judge on TypeSafe's wire, against a local stand-in that answers as TypeSafe's API reference documents
(``POST /v1/systemone``, a bearer key, Noul answers): a click on a control no pattern knows gets the flag the judge finds
while a known one is left to the patterns, a refused key makes the judge unavailable for the cooldown until a new key,
and a campaign claim the judge finds unlikely is refused before the goal checker. A real headless Chrome is used; no
model is called.
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
from pydantic import JsonValue

from dotagent.agent.gate import RunGate
from dotagent.agent.judge import JUDGE_UNAVAILABLE_EVENT, ClickFacts, ClickJudgment, TypeSafeJudge
from dotagent.agent.review import ReviewRequest, ReviewVerdict
from dotagent.agent.verify import new_facts
from dotagent.config import JudgeConfig, load_config
from dotagent.core.paths import workspace_dir
from dotagent.core.types import JsonObject
from dotagent.policy.types import ActionInfo
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import CampaignRecord, EventRecord, RunRecord, ThreadRecord
from dotagent.tools.base import PreparedCall, ToolResult
from dotagent.tools.campaign import CAMPAIGN_ACHIEVED_TOOL
from dotagent.tools.context import ToolContext

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

GOAL: str = "Grow the list of leads to 50."
KEY: str = "stand-in-key"
KEY_ENV: str = "DOT_TEST_TYPESAFE_KEY"
REFUSED_ENV: str = "DOT_TEST_TYPESAFE_REFUSED_KEY"
# The probability of yes the stand-in gives, by the label of the control and the question; anything else gets 0.05.
_YES: dict[str, dict[str, float]] = {"Boost": {"public_post": 0.95}}
_CLAIM_PROBABILITY: float = 0.4
_PAGE: bytes = b"""<!doctype html><title>Timeline</title><h1>Timeline</h1><p>A post worth sharing.</p>
<button type="button">Boost</button> <button type="button">Post</button> <a href="/next">Next page</a>"""


class _Service(ThreadingHTTPServer):
    """The fixture page and a stand-in for TypeSafe's API, which keeps the path, the authorization and the body of every
    request it receives."""

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.requests: list[tuple[str, str, JsonObject]] = []

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


def _answer(state: JsonValue, question: str) -> float:
    if question == "targets_reached":
        return _CLAIM_PROBABILITY
    control: JsonValue = state.get("control") if isinstance(state, dict) else None
    label: JsonValue = control.get("label") if isinstance(control, dict) else None
    return _YES.get(label, {}).get(question, 0.05) if isinstance(label, str) else 0.05


class _Handler(BaseHTTPRequestHandler):
    server: _Service

    def do_GET(self) -> None:
        page: bytes = _PAGE if self.path.partition("?")[0] == "/" else b"<!doctype html><p>Next</p>"
        self._send(200, "text/html; charset=utf-8", page)

    def do_POST(self) -> None:
        body: JsonObject = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        authorization: str = self.headers.get("Authorization", "")
        self.server.requests.append((self.path, authorization, body))
        if authorization != f"Bearer {KEY}":
            self._send(401, "application/json", b'{"error": "Missing or invalid API key."}')
            return
        questions: JsonValue = body["questions"]
        assert isinstance(questions, dict)
        answers: JsonObject = {
            question: {"type": "noul", "noul": _answer(body["state"], question)} for question in questions
        }
        reply: JsonObject = {
            "model": "jev-1.13.0",
            "answers": answers,
            "usage": {"input_tokens": 40, "output_tokens": 4},
        }
        self._send(200, "application/json", json.dumps(reply).encode())

    def _send(self, status: int, kind: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture(scope="module")
def service() -> Iterator[_Service]:
    server: _Service = _Service()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(service: _Service, tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[judge]\nprovider = "typesafe"\nbase_url = "{service.base}"\n'
        f'api_key_env = "{KEY_ENV}"\n',
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    # Both keys come from the environment, so no lookup reaches the Keychain.
    patch.setenv(KEY_ENV, KEY)
    patch.setenv(REFUSED_ENV, "a-key-the-stand-in-refuses")
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


async def _nobody(request: ReviewRequest) -> ReviewVerdict:
    raise AssertionError(f"the review model must not be asked about this: {request.action.summary}")


async def _context(runtime: Runtime, campaign_id: str | None) -> ToolContext:
    """A run in the store (a session of ``campaign_id`` when one is given) and the context its tools get."""
    thread: ThreadRecord = await runtime.store.create_thread("typed judge", "test")
    run: RunRecord = await runtime.store.create_run(
        thread.id, GOAL, "task", "ollama:none", None, "test", "medium", "ask", False, campaign_id
    )
    return ToolContext(
        runtime=runtime,
        run_id=run.id,
        thread_id=thread.id,
        mode="task",
        approvals="ask",
        questions="wait",
        workspace=workspace_dir(),
        gate=RunGate(
            runtime.store,
            runtime.bus,
            runtime.approvals,
            run.id,
            "task",
            "aggressive",
            runtime.config.policy.pace,
            "ask",
            _nobody,
            (GOAL,),
        ),
        cancel=asyncio.Event(),
        output_chars=runtime.config.agent.tool_output_chars,
        facts=new_facts,
    )


async def _classified(ctx: ToolContext, name: str, arguments: JsonObject) -> ActionInfo:
    prepared: PreparedCall = await ctx.runtime.registry.tools[name].prepare(arguments, ctx)
    return prepared.action


def _ref(snapshot: str, role: str, name: str) -> str:
    match: re.Match[str] | None = re.search(rf'{role} "{name}"[^\n]*?\[ref=([^\]]+)\]', snapshot)
    assert match is not None, (role, name, snapshot)
    return match.group(1)


async def test_a_click_no_pattern_knows_gets_the_flag_the_judge_finds_and_a_known_one_is_left_to_the_patterns(
    runtime: Runtime, service: _Service
) -> None:
    ctx: ToolContext = await _context(runtime, None)
    opened: PreparedCall = await runtime.registry.tools["browser_open"].prepare(
        {"url": f"{service.base}/?session=stand-in-session#top"}, ctx
    )
    page: ToolResult = await opened.run(ctx)
    refs: dict[str, str] = {
        name: _ref(page.text, role, name)
        for role, name in (("button", "Boost"), ("button", "Post"), ("link", "Next page"))
    }
    before: int = len(service.requests)
    boost: ActionInfo = await _classified(ctx, "browser_act", {"steps": [{"action": "click", "ref": refs["Boost"]}]})
    following: ActionInfo = await _classified(
        ctx, "browser_act", {"steps": [{"action": "click", "ref": refs["Next page"]}]}
    )
    asked: list[tuple[str, str, JsonObject]] = service.requests[before:]
    post: ActionInfo = await _classified(ctx, "browser_act", {"steps": [{"action": "click", "ref": refs["Post"]}]})
    assert (boost.risk, boost.flags) == ("medium", ("public_post",))
    assert boost.summary.endswith("(judge: public_post 0.95)"), boost.summary
    assert (following.risk, following.flags) == ("low", ())
    assert "judge" not in following.summary
    # The label a pattern knows is the patterns' alone: no third request.
    assert (post.flags, len(service.requests)) == (("public_post",), before + 2)
    path, authorization, body = asked[0]
    assert (path, authorization, body["model"]) == ("/v1/systemone", f"Bearer {KEY}", "jev-latest")
    # The page's query and fragment stay home.
    assert body["state"] == {"control": {"label": "Boost", "role": "button"}, "page": f"{service.base}/"}
    questions: JsonValue = body["questions"]
    assert isinstance(questions, dict)
    assert set(questions) == {"public_post", "external_send", "financial", "destructive"}
    assert all(isinstance(question, dict) and question["type"] == "noul" for question in questions.values())


async def test_a_refused_key_makes_the_judge_unavailable_for_the_cooldown_until_a_new_key(
    runtime: Runtime, service: _Service
) -> None:
    ctx: ToolContext = await _context(runtime, None)
    config: JudgeConfig = runtime.config.judge.model_copy(update={"api_key_env": REFUSED_ENV})
    judge: TypeSafeJudge = TypeSafeJudge(config, 600.0, runtime.bus)
    facts: ClickFacts = ClickFacts(label="Boost", role="button", url=f"{service.base}/")
    before: int = len(service.requests)
    try:
        first: ClickJudgment = await judge.judge_click(facts, ctx.run_id)
        second: ClickJudgment = await judge.judge_click(facts, ctx.run_id)
        while_paused: int = len(service.requests) - before
        # A key saved or removed in Settings retires the judge's connection and ends the pause.
        await judge.retire()
        third: ClickJudgment = await judge.judge_click(facts, ctx.run_id)
    finally:
        await judge.aclose()
    events: list[EventRecord] = await runtime.store.events_of_kind(ctx.run_id, JUDGE_UNAVAILABLE_EVENT, 10)
    assert first == second == third == ClickJudgment(reasons=(), note="judge off")
    # A refused key is not retried, and nothing is asked while the judge is unavailable.
    assert (while_paused, len(service.requests) - before) == (1, 2)
    assert [event.data["reason"] for event in events] == ["key refused", "key refused"]
    assert all(str(event.data["error"]).startswith("provider answered HTTP 401") for event in events)


async def test_a_campaign_claim_the_judge_finds_unlikely_is_refused_before_the_goal_checker(
    runtime: Runtime, service: _Service
) -> None:
    thread: ThreadRecord = await runtime.store.create_thread("leads", "test")
    campaign: CampaignRecord = await runtime.store.create_campaign(
        "Leads", GOAL, thread.id, "ollama:none", "medium", "ask", ()
    )
    await runtime.store.set_campaign_targets(campaign.id, "50 leads in leads.csv", "wc -l leads.csv counts 51 lines")
    ctx: ToolContext = await _context(runtime, campaign.id)
    claim: str = "leads.csv has 50 leads."
    prepared: PreparedCall = await runtime.registry.tools[CAMPAIGN_ACHIEVED_TOOL].prepare({"evidence": claim}, ctx)
    result: ToolResult = await prepared.run(ctx)
    stored: CampaignRecord = await runtime.store.get_campaign(campaign.id)
    _, _, body = service.requests[-1]
    assert result.is_error
    assert result.text.startswith("Not accepted: the typed judge gives 0.40 for the targets being reached"), result.text
    assert stored.status == "active"
    state: JsonValue = body["state"]
    assert isinstance(state, dict) and isinstance(body["questions"], dict)
    assert list(body["questions"]) == ["targets_reached"]
    assert state["claim"] == claim and "50 leads in leads.csv" in str(state["targets"])
