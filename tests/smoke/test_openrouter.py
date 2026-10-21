"""OpenRouter (item 349) against a stand-in for its API on this Mac, so nothing leaves it.

- A run on an OpenRouter model goes through the real loop: it lists the model's traits, sends the reasoning level as
  OpenRouter's ``reasoning`` object with the key as a Bearer token, calls a tool, and on its next step carries the
  reasoning details of the first step back to the same model, joined from their streamed pieces.
- The connector lists the models that call tools as such, reads what its key has spent, takes the words of a thought
  sent only as reasoning details, and says why a call failed: credits that ran out inside the stream, and a refused key.
"""

import asyncio
import json
import threading
from collections.abc import AsyncIterator, Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import OpenRouterConfig, load_config, load_default_config
from dotagent.core.errors import ProviderAuthError, ProviderCreditError, ProviderQuotaError
from dotagent.core.types import JsonObject
from dotagent.llm.openrouter import KeyStatus, OpenRouterProvider
from dotagent.llm.types import ChatRequest, ModelInfo, ModelReply, StreamDelta, ToolSpec, user_message
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL

pytestmark = pytest.mark.asyncio(loop_scope="module")

_KEY: str = "sk-or-stand-in"
_MODEL: str = "lab/agent-model"
_RUN_WAIT_S: float = 60.0
# The model a run works with, as OpenRouter lists it (2026-10-17's shape), and one that calls no tools.
_LISTING: JsonObject = {
    "data": [
        {
            "id": _MODEL,
            "context_length": 200_000,
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
            "pricing": {"prompt": "0.000001", "completion": "0.000002"},
            "top_provider": {"max_completion_tokens": 64_000},
            "supported_parameters": ["max_tokens", "reasoning", "temperature", "tools"],
            "reasoning": {"mandatory": True, "supported_efforts": ["high", "medium", "low"]},
        },
        {
            "id": "lab/talker:free",
            "context_length": 32_000,
            "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
            "pricing": {"prompt": "0", "completion": "0"},
            "top_provider": {"max_completion_tokens": 4_096},
            "supported_parameters": ["max_tokens", "temperature"],
        },
    ]
}
# The thought of the first step, in the pieces OpenRouter streams it in, and as a whole.
_PIECES: list[JsonObject] = [
    {"type": "reasoning.text", "text": "First ", "index": 0, "format": "anthropic-claude-v1"},
    {"type": "reasoning.text", "text": "I look.", "index": 0},
    {"type": "reasoning.text", "signature": "sig-1", "index": 0},
]
_WHOLE: JsonObject = {
    "type": "reasoning.text",
    "text": "First I look.",
    "index": 0,
    "format": "anthropic-claude-v1",
    "signature": "sig-1",
}


def _chunk(delta: JsonObject, finish: str | None) -> JsonObject:
    return {"choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def _call(name: str, arguments: JsonObject) -> JsonObject:
    return {
        "tool_calls": [
            {
                "index": 0,
                "id": f"call_{name}",
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ]
    }


# How OpenRouter words a 402 for a request whose longest answer the credits do not pay for (2026-10-19).
_CREDITS: str = (
    "This request requires more credits, or fewer max_tokens. "
    "You requested up to 131072 tokens, but can only afford {}."
)
_USAGE: JsonObject = {
    "choices": [],
    "usage": {"prompt_tokens": 900, "completion_tokens": 40, "prompt_tokens_details": {"cached_tokens": 512}},
}
_STEPS: list[list[JsonObject]] = [
    [
        *(_chunk({"reasoning_details": [piece]}, None) for piece in _PIECES),
        _chunk(_call("list_dir", {"path": "."}), None),
        _chunk({}, "tool_calls"),
        _USAGE,
    ],
    [_chunk(_call(FINISH_TOOL, {"answer": "Done.", "success": True}), None), _chunk({}, "tool_calls"), _USAGE],
]


class StandIn:
    """What the stand-in was asked: the bodies and the authorization of the chat requests that carried tools."""

    def __init__(self) -> None:
        self.steps: list[JsonObject] = []
        self.authorizations: list[str] = []
        self.key_refused: bool = False
        self.out_of_credits: bool = False
        # The longest answer the stand-in's credits pay for: a chat request that allows a longer one is refused (402).
        self.affordable: int | None = None
        self.priced: list[object] = []


def _handler(state: StandIn) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _json(self, status: int, payload: JsonObject) -> None:
            body: bytes = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _stream(self, chunks: list[JsonObject]) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b": OPENROUTER PROCESSING\n\n")
            for chunk in chunks:
                self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.write(b"data: [DONE]\n\n")

        def do_GET(self) -> None:
            if self.path == "/api/v1/models":
                self._json(200, _LISTING)
            elif self.path == "/api/v1/key" and state.key_refused:
                self._json(401, {"error": {"message": "No auth credentials found", "code": 401}})
            elif self.path == "/api/v1/key":
                self._json(200, {"data": {"label": "sk-or-…", "usage": 1.5, "limit": 10, "is_free_tier": False}})
            else:
                self._json(404, {"error": {"message": "not found", "code": 404}})

        def do_POST(self) -> None:
            body: JsonObject = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if state.affordable is not None:
                asked = body.get("max_tokens")
                state.priced.append(asked)
                if not isinstance(asked, int) or asked > state.affordable:
                    self._json(402, {"error": {"code": 402, "message": _CREDITS.format(state.affordable)}})
                    return
            if state.out_of_credits:
                self._stream(
                    [
                        {
                            "error": {"code": 402, "message": "Insufficient credits"},
                            "choices": [{"index": 0, "delta": {}, "finish_reason": "error"}],
                        }
                    ]
                )
                return
            if "tools" not in body:
                # Dot's side work (a title, a summary): a plain answer.
                self._stream([_chunk({"content": "Noted."}, None), _chunk({}, "stop")])
                return
            state.steps.append(body)
            state.authorizations.append(self.headers["Authorization"])
            # The first step thinks and calls a tool; once the tool's result is in the conversation, the run finishes.
            answered: bool = any(message.get("role") == "tool" for message in _messages(body))
            self._stream(_STEPS[1] if answered else _STEPS[0])

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


@pytest.fixture(scope="module")
def stand_in() -> Iterator[tuple[StandIn, str]]:
    state: StandIn = StandIn()
    server: ThreadingHTTPServer = ThreadingHTTPServer(("127.0.0.1", 0), _handler(state))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield state, f"http://127.0.0.1:{server.server_address[1]}/api/v1"
    finally:
        server.shutdown()


def _home(root: Path, base_url: str) -> Path:
    home: Path = root / "home"
    home.mkdir()
    model: str = f"openrouter:{_MODEL}"
    roles: str = "".join(f'{role} = "{model}"\n' for role in ("main", "fast", "review", "vision"))
    (home / "config.toml").write_text(
        f'[models]\n{roles}\n[memory]\nembed_enabled = false\n\n[agent]\nlessons = "off"\nplaybooks = false\n\n'
        f'[skills]\nelsewhere = false\n\n[providers.ollama]\nbase_url = "http://127.0.0.1:9"\n\n'
        f'[providers.openrouter]\nbase_url = "{base_url}"\n',
        encoding="utf-8",
    )
    return home


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(stand_in: tuple[StandIn, str], tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = await asyncio.to_thread(_home, tmp_path_factory.mktemp("openrouter"), stand_in[1])
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    patch.setenv("OPENROUTER_API_KEY", _KEY)
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


async def test_a_run_on_an_openrouter_model_carries_its_reasoning_back(
    runtime: Runtime, stand_in: tuple[StandIn, str]
) -> None:
    state, _ = stand_in
    before: int = len(state.steps)
    manager: RunManager = RunManager(runtime)
    try:
        run: RunRecord = await manager.start(
            "List the workspace",
            "task",
            f"openrouter:{_MODEL}",
            None,
            "test",
            None,
            None,
            "standard",
            None,
            "yolo",
            None,
            (),
        )
        async with asyncio.timeout(_RUN_WAIT_S):
            ended: RunRecord = await manager.wait(run.id)
    finally:
        await manager.shutdown()
    assert ended.status == "done", ended.result
    steps: list[JsonObject] = state.steps[before:]
    assert len(steps) == 2
    assert state.authorizations[before:] == [f"Bearer {_KEY}"] * 2
    assert all(step["reasoning"] == {"effort": "medium"} and "reasoning_effort" not in step for step in steps)
    assert not [message for message in _messages(steps[0]) if "reasoning_details" in message]
    turns: list[JsonObject] = [message for message in _messages(steps[1]) if message.get("role") == "assistant"]
    assert [turn.get("reasoning_details") for turn in turns] == [[_WHOLE]]


async def test_the_connector_lists_reads_its_key_and_says_why_a_call_failed(stand_in: tuple[StandIn, str]) -> None:
    state, base_url = stand_in
    config: OpenRouterConfig = load_default_config().providers.openrouter.model_copy(
        update={"base_url": base_url, "attempts": 1}
    )
    provider: OpenRouterProvider = OpenRouterProvider(config, _KEY)
    try:
        listed: tuple[ModelInfo, ...] = await provider.list_models()
        assert [(info.id, info.tools, info.free, info.efforts) for info in listed] == [
            ("lab/agent-model", True, False, ("low", "medium", "high")),
            ("lab/talker:free", False, True, ()),
        ]
        status: KeyStatus = await provider.key_status()
        assert (status.usage, status.limit, status.free_tier) == (1.5, 10.0, False)
        deltas: list[StreamDelta] = []

        async def sink(delta: StreamDelta) -> None:
            deltas.append(delta)

        reply: ModelReply = await provider.complete(_ask(), sink)
        assert reply.thinking == "First I look." and reply.raw == (_WHOLE,)
        assert [delta.text for delta in deltas if delta.kind == "thinking"] == ["First ", "I look."]
        assert (reply.usage.input_tokens, reply.usage.output_tokens, reply.usage.cached_tokens) == (900, 40, 512)
        state.out_of_credits = True
        with pytest.raises(ProviderQuotaError):
            await provider.complete(_ask(), sink)
        state.out_of_credits = False
        state.key_refused = True
        with pytest.raises(ProviderAuthError):
            await provider.key_status()
        state.key_refused = False
    finally:
        await provider.aclose()


async def test_a_402_that_names_the_answer_the_credits_pay_for_is_asked_again_with_that_max_tokens(
    stand_in: tuple[StandIn, str],
) -> None:
    """Dot sends no ``max_tokens``, so OpenRouter prices the call at the model's longest answer: an account with few
    credits is refused a call whose answer would be short. The connector asks once more with what the 402 names, and
    raises the 402 when it names less than an answer is worth."""
    state, base_url = stand_in
    config: OpenRouterConfig = load_default_config().providers.openrouter.model_copy(
        update={"base_url": base_url, "attempts": 1}
    )
    provider: OpenRouterProvider = OpenRouterProvider(config, _KEY)

    async def sink(delta: StreamDelta) -> None:
        return

    try:
        state.affordable = 29_597
        state.priced.clear()
        reply: ModelReply = await provider.complete(_ask(), sink)
        assert reply.thinking == "First I look."
        assert state.priced == [None, 29_597]
        state.affordable = 100
        state.priced.clear()
        with pytest.raises(ProviderCreditError) as refused:
            await provider.complete(_ask(), sink)
        assert refused.value.affordable_tokens == 100 and state.priced == [None]
    finally:
        state.affordable = None
        await provider.aclose()


_LIST_DIR: ToolSpec = ToolSpec(
    name="list_dir",
    description="List a folder.",
    parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
)


def _ask() -> ChatRequest:
    return ChatRequest(
        model=_MODEL,
        messages=(user_message("List the workspace", ()),),
        tools=(_LIST_DIR,),
        temperature=None,
        max_output_tokens=None,
        effort="medium",
        speed="standard",
        session_key="",
        cache_mark=None,
        window=None,
    )


def _messages(body: JsonObject) -> list[JsonObject]:
    messages = body["messages"]
    assert isinstance(messages, list)
    return [message for message in messages if isinstance(message, dict)]
