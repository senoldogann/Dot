"""The typed judge and the judge_items tool on TypeSafe's wire, against a local stand-in that answers as TypeSafe's API
reference documents (``POST /v1/systemone``, a bearer key, Noul and Score answers, 422 for a body it rejects): a click
on a control no pattern knows gets the flag the judge finds while a known one is left to the patterns, a refused key
makes the judge unavailable for the cooldown until a new key, a campaign claim the judge finds unlikely is refused
before the goal checker, judge_items ranks a list with one question per item in one request, and a rejected request is
an error event that pauses nothing. A real headless Chrome is used; no model is called.
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
from dotagent.agent.judge import (
    JUDGE_ERROR_EVENT,
    JUDGE_UNAVAILABLE_EVENT,
    ClickFacts,
    ClickJudgment,
    TypeSafeJudge,
    TypeSafeService,
)
from dotagent.agent.review import ReviewRequest, ReviewVerdict
from dotagent.agent.verify import new_facts
from dotagent.config import JudgeConfig, load_config
from dotagent.core.paths import workspace_dir
from dotagent.core.types import JsonObject
from dotagent.policy.types import ActionInfo
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import CampaignEntry, CampaignRecord, EventRecord, RunRecord, ThreadRecord
from dotagent.tools.base import PreparedCall, ToolResult
from dotagent.tools.campaign import CAMPAIGN_ACHIEVED_TOOL, CAMPAIGN_NOTE_TOOL
from dotagent.tools.context import ToolContext
from dotagent.tools.judge_items import JUDGE_ITEMS_TOOL
from dotagent.tools.posts import CHECK_DRAFT_TOOL, PICK_DRAFT_TOOL, POSTS_RANKED_EVENT, RANK_POSTS_TOOL

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

GOAL: str = "Grow the list of leads to 50."
KEY: str = "stand-in-key"
KEY_ENV: str = "DOT_TEST_TYPESAFE_KEY"
REFUSED_ENV: str = "DOT_TEST_TYPESAFE_REFUSED_KEY"
# The probability of yes the stand-in gives, by the label of the control and the question; anything else gets 0.05.
_YES: dict[str, dict[str, float]] = {"Boost": {"public_post": 0.95}}
_CLAIM_PROBABILITY: float = 0.1
# The stand-in finds a candidate of judge_items on topic when its text has this word; a question with this mark is a
# body it rejects, as TypeSafe does with HTTP 422.
_TOPIC: str = "python"
_REJECTED: str = "[reject]"
_LEVELS: tuple[str, ...] = ("unrelated", "mentions Python in passing", "about Python")
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


def _candidate(state: JsonValue, question: str) -> str | None:
    candidates: JsonValue = state.get("candidates") if isinstance(state, dict) else None
    text: JsonValue = candidates.get(question) if isinstance(candidates, dict) else None
    return text if isinstance(text, str) else None


def _post_text(state: JsonValue, question: str) -> str | None:
    """The text of the post a rank_posts question (``<id>__<weight>``) is about."""
    posts: JsonValue = state.get("posts") if isinstance(state, dict) else None
    post: JsonValue = posts.get(question.split("__")[0]) if isinstance(posts, dict) else None
    text: JsonValue = post.get("text") if isinstance(post, dict) else None
    return text if isinstance(text, str) else None


def _post_answer(text: str, question: str) -> float:
    if question.endswith("__w_topic"):
        return 0.9 if _TOPIC in text.lower() else 0.1
    if question.endswith("__w_person"):
        return 0.1 if "giveaway" in text.lower() else 0.9
    return 0.8 if "?" in text else 0.3


def _check_answer(state: dict[str, JsonValue], question: str) -> float:
    """check_draft's stand-in: a draft without the topic is off topic, one equal to an earlier post repeats it, and a
    link breaks a rule that mentions links."""
    draft: str = str(state["draft"])
    if question == "off_topic":
        return 0.1 if _TOPIC in draft.lower() else 0.9
    if question == "repeats":
        earlier: JsonValue = state["earlier_posts"]
        return 0.9 if isinstance(earlier, list) and draft in earlier else 0.1
    rules: JsonValue = state["rules"]
    assert isinstance(rules, list)
    rule: str = str(rules[int(question.removeprefix("rule_"))])
    return 0.9 if "http" in draft and "link" in rule else 0.1


def _choice(criteria: dict[str, JsonValue]) -> JsonObject:
    """The stand-in picks the longest option; the probabilities follow the lengths."""
    lengths: dict[str, int] = {option: len(str(text)) for option, text in criteria.items()}
    total: int = sum(lengths.values())
    probabilities: dict[str, float] = {option: length / total for option, length in lengths.items()}
    best: str = max(lengths, key=lengths.__getitem__)
    return {"type": "choice", "choice": best, "confidence": probabilities[best], "probabilities": probabilities}


def _answer(state: JsonValue, question: str) -> float:
    if question == "targets_reached":
        return _CLAIM_PROBABILITY
    post: str | None = _post_text(state, question)
    if post is not None:
        return _post_answer(post, question)
    if isinstance(state, dict) and "draft" in state:
        return _check_answer(state, question)
    candidate: str | None = _candidate(state, question)
    if candidate is not None:
        return 0.9 if _TOPIC in candidate.lower() else 0.1
    control: JsonValue = state.get("control") if isinstance(state, dict) else None
    label: JsonValue = control.get("label") if isinstance(control, dict) else None
    return _YES.get(label, {}).get(question, 0.05) if isinstance(label, str) else 0.05


def _score(state: JsonValue, question: str, criteria: list[str]) -> JsonObject:
    candidate: str | None = _candidate(state, question)
    on_topic: bool = candidate is not None and _TOPIC in candidate.lower()
    weights: list[float] = [0.1, 0.1, 0.8] if on_topic else [0.7, 0.3, 0.0]
    return {
        "type": "score",
        "score": sum(level * weight for level, weight in enumerate(weights)),
        "confidence": max(weights),
        "legend": {str(level): text for level, text in enumerate(criteria)},
        "probabilities": {str(level): weight for level, weight in enumerate(weights)},
    }


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
        if any(isinstance(spec, dict) and _REJECTED in str(spec["instructions"]) for spec in questions.values()):
            detail: bytes = json.dumps({"detail": [{"loc": ["body", "questions"], "msg": "rejected"}]}).encode()
            self._send(422, "application/json", detail)
            return
        answers: JsonObject = {}
        for question, spec in questions.items():
            assert isinstance(spec, dict)
            if spec["type"] == "score":
                criteria: JsonValue = spec["criteria"]
                assert isinstance(criteria, list) and all(isinstance(level, str) for level in criteria)
                answers[question] = _score(body["state"], question, [str(level) for level in criteria])
            elif spec["type"] == "choice":
                options: JsonValue = spec["criteria"]
                assert isinstance(options, dict)
                answers[question] = _choice(options)
            else:
                answers[question] = {"type": "noul", "noul": _answer(body["state"], question)}
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
        f'api_key_env = "{KEY_ENV}"\nitems_per_request = 3\n',
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
        browser=runtime.browser,
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
            runtime.config.policy.spend,
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
    assert (path, authorization, body["model"]) == ("/v1/systemone", f"Bearer {KEY}", runtime.config.judge.model)
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
    judge: TypeSafeJudge = TypeSafeJudge(TypeSafeService(config, 600.0, runtime.bus), config)
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
        "Leads", GOAL, thread.id, "ollama:none", "medium", "ask", (), "own"
    )
    await runtime.store.set_campaign_targets(campaign.id, "50 leads in leads.csv", "wc -l leads.csv counts 51 lines")
    ctx: ToolContext = await _context(runtime, campaign.id)
    claim: str = "leads.csv has 50 leads."
    prepared: PreparedCall = await runtime.registry.tools[CAMPAIGN_ACHIEVED_TOOL].prepare({"evidence": claim}, ctx)
    result: ToolResult = await prepared.run(ctx)
    stored: CampaignRecord = await runtime.store.get_campaign(campaign.id)
    _, _, body = service.requests[-1]
    assert result.is_error
    assert result.text.startswith("Not accepted: the typed judge gives 0.10 for the targets being reached"), result.text
    assert stored.status == "active"
    state: JsonValue = body["state"]
    assert isinstance(state, dict) and isinstance(body["questions"], dict)
    assert list(body["questions"]) == ["targets_reached"]
    assert state["claim"] == claim and "50 leads in leads.csv" in str(state["targets"])


_POSTS: list[JsonObject] = [
    {"id": "p1", "text": "uv 0.5 ships a lockfile format that replaces requirements.txt (Python)"},
    {"id": "p2", "text": "My cat learned to open the fridge"},
    {"id": "p3", "text": "Why Python wheels beat sdists: a packaging story"},
    {"id": "p4", "text": "Weekend hike photos from the Alps"},
]


async def test_judge_items_ranks_a_list_with_one_question_per_item_in_requests_of_the_configured_size(
    runtime: Runtime, service: _Service
) -> None:
    ctx: ToolContext = await _context(runtime, None)
    before: int = len(service.requests)
    question: str = "Is this post about Python?"
    prepared: PreparedCall = await runtime.registry.tools[JUDGE_ITEMS_TOOL].prepare(
        {"question": question, "items": _POSTS}, ctx
    )
    assert (prepared.action.kind, prepared.action.risk, prepared.action.flags) == ("network", "low", ())
    assert prepared.action.summary == f"Judge 4 items with TypeSafe: {question}"
    result: ToolResult = await prepared.run(ctx)
    assert not result.is_error
    lines: list[str] = result.text.splitlines()
    # Four items at three per request: two requests, the on-topic posts first.
    assert lines[0] == f"4 items judged on “{question}” in 2 requests; the probability of yes, the likeliest yes first:"
    assert [line.split()[1] for line in lines[1:]] == ["p1", "p3", "p2", "p4"]
    assert lines[1] == "1. p1 0.90 — uv 0.5 ships a lockfile format that replaces requirements.t…"
    assert len(service.requests) == before + 2
    path, authorization, body = service.requests[before]
    assert (path, authorization, body["model"]) == ("/v1/systemone", f"Bearer {KEY}", runtime.config.judge.model)
    assert body["state"] == {"candidates": {post["id"]: post["text"] for post in _POSTS[:3]}}
    questions: JsonValue = body["questions"]
    assert isinstance(questions, dict) and list(questions) == ["p1", "p2", "p3"]
    assert questions["p3"] == {"type": "noul", "instructions": f"About the candidate at `candidates.p3`: {question}"}
    _, _, second = service.requests[before + 1]
    assert second["state"] == {"candidates": {"p4": _POSTS[3]["text"]}}
    # With levels the same list is rated: a Score question per item, its criteria the levels, the likeliest named.
    rated: PreparedCall = await runtime.registry.tools[JUDGE_ITEMS_TOOL].prepare(
        {"question": "How much is this post about Python?", "items": _POSTS[:3], "levels": [*_LEVELS]}, ctx
    )
    scores: ToolResult = await rated.run(ctx)
    assert not scores.is_error
    scored: list[str] = scores.text.splitlines()
    assert scored[0].startswith(
        "3 items rated on “How much is this post about Python?” in 1 request; the expected level"
    )
    assert scored[1:] == [
        "1. p1 1.70 (about Python) — uv 0.5 ships a lockfile format that replaces requirements.t…",
        "2. p3 1.70 (about Python) — Why Python wheels beat sdists: a packaging story",
        "3. p2 0.30 (unrelated) — My cat learned to open the fridge",
    ]
    _, _, rated_body = service.requests[-1]
    rated_questions: JsonValue = rated_body["questions"]
    assert isinstance(rated_questions, dict)
    assert rated_questions["p2"] == {
        "type": "score",
        "instructions": "About the candidate at `candidates.p2`: How much is this post about Python?",
        "criteria": [*_LEVELS],
    }


async def test_a_request_typesafe_rejects_is_an_error_event_and_pauses_neither_the_tool_nor_the_judge(
    runtime: Runtime, service: _Service
) -> None:
    ctx: ToolContext = await _context(runtime, None)
    rejected: PreparedCall = await runtime.registry.tools[JUDGE_ITEMS_TOOL].prepare(
        {"question": f"{_REJECTED} Is this about Python?", "items": _POSTS[:2]}, ctx
    )
    result: ToolResult = await rejected.run(ctx)
    assert result.is_error
    assert result.text.startswith("TypeSafe rejected the request or answered outside its reference"), result.text
    assert "HTTP 422" in result.text
    errors: list[EventRecord] = await runtime.store.events_of_kind(ctx.run_id, JUDGE_ERROR_EVENT, 10)
    paused: list[EventRecord] = await runtime.store.events_of_kind(ctx.run_id, JUDGE_UNAVAILABLE_EVENT, 10)
    assert len(errors) == 1 and paused == []
    assert str(errors[0].data["error"]).startswith("provider answered HTTP 422")
    # The next request goes out: the judge still answers, and so does the tool.
    before: int = len(service.requests)
    facts: ClickFacts = ClickFacts(label="Boost", role="button", url=f"{service.base}/")
    judgment: ClickJudgment = await runtime.judge.judge_click(facts, ctx.run_id)
    assert judgment.reasons == ("post",)
    again: PreparedCall = await runtime.registry.tools[JUDGE_ITEMS_TOOL].prepare(
        {"question": "Is this about Python?", "items": _POSTS[:2]}, ctx
    )
    assert not (await again.run(ctx)).is_error
    assert len(service.requests) == before + 2


_FEED: list[JsonObject] = [
    {"id": "a", "author": "@mara", "text": "Lost an afternoon to uv lock versus pip freeze in Python. Never again."},
    {"id": "b", "author": "@dealsbot", "text": "GIVEAWAY: retweet to win a laptop!"},
    {"id": "c", "author": "@tomas", "text": "Which grinder do you use for espresso under 300 euros?"},
]


async def _campaign_context(runtime: Runtime) -> tuple[ToolContext, CampaignRecord]:
    thread: ThreadRecord = await runtime.store.create_thread("x", "test")
    campaign: CampaignRecord = await runtime.store.create_campaign(
        "Python on X",
        "Grow @dot_python on X to 500 followers. Rules: no links.",
        thread.id,
        "ollama:none",
        "medium",
        "ask",
        (),
        "own",
    )
    return await _context(runtime, campaign.id), campaign


async def _run(ctx: ToolContext, tool: str, arguments: JsonObject) -> ToolResult:
    prepared: PreparedCall = await ctx.runtime.registry.tools[tool].prepare(arguments, ctx)
    return await prepared.run(ctx)


async def test_a_campaign_session_ranks_posts_with_the_weights_of_its_ledger_and_keeps_the_scores(
    runtime: Runtime, service: _Service
) -> None:
    ctx, campaign = await _campaign_context(runtime)
    before: int = len(service.requests)
    ranked: ToolResult = await _run(ctx, RANK_POSTS_TOOL, {"topic": "Python", "posts": _FEED})
    assert not ranked.is_error, ranked.text
    lines: list[str] = ranked.text.splitlines()
    # Three questions per post and three questions per request (the fixture's items_per_request): one post a request.
    assert lines[0].startswith("3 posts ranked in 3 requests by the weighted probability of yes to three questions")
    assert "(on the topic ×1, by a person ×1, worth a reply ×1;" in lines[0]
    # a: on topic, a person, no question (0.9, 0.9, 0.3); c: off topic, a person, a question (0.1, 0.9, 0.8); b: spam.
    assert [line.split()[1] for line in lines[1:]] == ["a", "c", "b"]
    assert lines[1] == (
        "1. a 0.70 (topic 0.90, person 0.90, reply 0.30) @mara: Lost an afternoon to uv lock versus pip freeze in Python. N…"
    )
    # The first ranking wrote the weights in the ledger, where the owner changes them.
    entries: list[CampaignEntry] = await runtime.store.campaign_entries(campaign.id, 10)
    assert [(entry.kind, entry.numbers) for entry in entries] == [
        ("note", {"w_topic": 1.0, "w_person": 1.0, "w_reply": 1.0})
    ]
    assert entries[0].text.startswith("Weights of rank_posts:")
    # The last request asks the three questions about the last post, by its id in the state.
    assert len(service.requests) == before + 3
    _, _, body = service.requests[-1]
    questions: JsonValue = body["questions"]
    assert isinstance(questions, dict) and list(questions) == ["c__w_topic", "c__w_person", "c__w_reply"]
    assert questions["c__w_reply"] == {
        "type": "noul",
        "instructions": (
            "Is the post at `posts.c` worth a reply that adds something, such as an answer, an example or a question, "
            "because it asks, claims or describes a problem, rather than a closed statement, a joke or an announcement?"
        ),
    }
    state: JsonValue = body["state"]
    assert isinstance(state, dict) and state["topic"] == "Python"
    assert state["posts"] == {"c": {"author": _FEED[2]["author"], "text": _FEED[2]["text"]}}
    events: list[EventRecord] = await runtime.store.events_of_kind(ctx.run_id, POSTS_RANKED_EVENT, 10)
    assert len(events) == 1 and events[0].data["weights"] == {"w_topic": 1.0, "w_person": 1.0, "w_reply": 1.0}
    posts: JsonValue = events[0].data["posts"]
    assert isinstance(posts, list) and [post["id"] for post in posts if isinstance(post, dict)] == ["a", "c", "b"]
    # A ledger entry with new weights changes the ranking without a change of the questions: replies count four times.
    noted: ToolResult = await _run(
        ctx,
        CAMPAIGN_NOTE_TOOL,
        {
            "kind": "note",
            "text": "Replies matter most.",
            "numbers": [
                {"name": "w_topic", "value": 1},
                {"name": "w_person", "value": 1},
                {"name": "w_reply", "value": 4},
            ],
        },
    )
    assert not noted.is_error
    again: ToolResult = await _run(ctx, RANK_POSTS_TOOL, {"topic": "Python", "posts": _FEED})
    assert [line.split()[1] for line in again.text.splitlines()[1:]] == ["c", "a", "b"]
    assert "worth a reply ×4;" in again.text


async def test_a_campaign_session_picks_the_best_draft_and_a_draft_with_a_link_is_refused_by_the_rule(
    runtime: Runtime, service: _Service
) -> None:
    ctx, _ = await _campaign_context(runtime)
    drafts: list[JsonObject] = [
        {"id": "d1", "text": "Same here."},
        {
            "id": "d2",
            "text": "uv locks per platform while pip freeze reads one environment; that is the whole difference.",
        },
    ]
    picked: ToolResult = await _run(ctx, PICK_DRAFT_TOOL, {"topic": "Python", "post": _FEED[0], "drafts": drafts})
    assert not picked.is_error
    assert picked.text.startswith(
        "Best draft: d2 (probability 0.90, confidence 0.90). All drafts, the likeliest first:\nd2 0.90"
    )
    _, _, body = service.requests[-1]
    questions: JsonValue = body["questions"]
    assert isinstance(questions, dict) and list(questions) == ["best"]
    best: JsonValue = questions["best"]
    assert isinstance(best, dict) and best["type"] == "choice"
    assert best["criteria"] == {"d1": "Same here.", "d2": drafts[1]["text"]}
    check: JsonObject = {
        "topic": "Python",
        "post": _FEED[0],
        "draft": "See how uv does it in Python: https://example.com/uv",
        "earlier_posts": ["uv locks per platform."],
        "rules": ["no links in replies", "never sell"],
    }
    refused: ToolResult = await _run(ctx, CHECK_DRAFT_TOOL, check)
    assert refused.is_error
    assert refused.text.splitlines() == [
        "Do not post the draft as it is: 1 of 4 checks reached 0.50. Change it and check again, or answer another post.",
        "- off the topic or beside the point of the post: 0.10",
        "- repeats an earlier post of the account: 0.10",
        "- breaks the rule: no links in replies: 0.90 (fired)",
        "- breaks the rule: never sell: 0.10",
    ]
    _, _, checked = service.requests[-1]
    asked: JsonValue = checked["questions"]
    assert isinstance(asked, dict) and list(asked) == ["off_topic", "repeats", "rule_0", "rule_1"]
    passed: ToolResult = await _run(
        ctx, CHECK_DRAFT_TOOL, {**check, "draft": "uv locks per platform in Python; pip freeze reads one environment."}
    )
    assert not passed.is_error
    assert passed.text.startswith("The draft passes its 4 checks (none reached 0.50); the decision to post is yours.")
