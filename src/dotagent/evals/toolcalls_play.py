"""Playing the eval of tool use (item 736): in a throw-away home, each case's goal is started as a task run of the
scripted model, whose first request is kept as Dot made it (the system prompt and the tools' schemas a run of that goal
gets); the steps before the case's moment are put after it, and the model under test is asked, again while it only
plans (:data:`dotagent.evals.toolcalls.EXTRA_TURNS` times at most). The request is captured in ``yolo``, the mode that
asks nothing, so that no case waits on an approval: the prompt of another mode may differ in its lines on approvals.
The model's keys are read from the environment and the home's ``.env`` file alone, and the Keychain is not asked while
the cases play (:func:`no_keychain`): a key the runtime looks for and finds in neither is missing, not looked up in the
Mac's login keychain."""

import asyncio
import os
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager

from dotagent import secrets
from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig
from dotagent.core.errors import DotError
from dotagent.core.paths import env_file_path
from dotagent.evals.runner import SCRIPTED_MODEL, eval_environment, scratch_home, secret_names
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.toolcalls import (
    EXTRA_TURNS,
    CaseResult,
    ToolCase,
    case_request,
    first_work,
    judge,
    meta_answers,
    prior_messages,
)
from dotagent.evals.types import ScriptedCall, ScriptedTurn
from dotagent.llm.calls import CallOrigin
from dotagent.llm.router import ModelRef, parse_model_ref
from dotagent.llm.types import (
    ChatProvider,
    ChatRequest,
    DeltaSink,
    Message,
    ModelInfo,
    ModelReply,
    StreamDelta,
    ToolCall,
)
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.secrets import parse_env_file
from dotagent.store.models import RunRecord
from dotagent.tools.meta import FINISH_TOOL

_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_FINISH: ScriptedTurn = ScriptedTurn(
    text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Captured.", "success": True}),)
)
_CAPTURE_S: float = 30.0
_ORIGIN: CallOrigin = CallOrigin(run_id=None, purpose="step")


class _Keeping:
    """The scripted model, keeping the first request of each run it plays."""

    def __init__(self) -> None:
        self._script: ScriptedProvider = ScriptedProvider(_PROVIDER, [])
        self.first: ChatRequest | None = None

    @property
    def name(self) -> str:
        return self._script.name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        if self.first is None:
            self.first = request
        self._script = ScriptedProvider(_PROVIDER, [_FINISH])
        return await self._script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await self._script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self._script.list_models()

    async def aclose(self) -> None:
        await self._script.aclose()


def keys_for(config: AppConfig, model: str) -> dict[str, str]:
    """The keys ``model``'s provider needs, from the environment and the home's ``.env`` file alone (never the
    Keychain); a key found in neither is left out, and the provider says it is missing when asked."""
    path = env_file_path()
    kept: dict[str, str] = parse_env_file(path.read_text(encoding="utf-8")) if path.exists() else {}
    found: dict[str, str] = {}
    for name in secret_names(config, (model,)):
        value: str | None = os.environ.get(name) or kept.get(name)
        if value:
            found[name] = value
    return found


@contextmanager
def no_keychain() -> Iterator[None]:
    """The Keychain is not asked in this process until the block ends: a key that is in neither the environment nor
    the home's ``.env`` is missing (the runtime's optional keys, a provider's), as the tests make it."""
    # The one place the lookup is made, replaced as the tests replace it (tests/conftest.py).
    asked: object = getattr(secrets, _LOOKUP)
    setattr(secrets, _LOOKUP, _none)
    try:
        yield
    finally:
        setattr(secrets, _LOOKUP, asked)


# The function of :mod:`dotagent.secrets` that asks the Keychain.
_LOOKUP: str = "_from_keychain"


def _none(_name: str) -> None:
    return None


async def _quiet(_delta: StreamDelta) -> None:
    return None


async def _captured(runtime: Runtime, manager: RunManager, keeping: _Keeping, goal: str) -> ChatRequest:
    """The first request a task run of ``goal`` sends, as Dot makes it."""
    keeping.first = None
    run: RunRecord = await manager.start(
        goal, "task", SCRIPTED_MODEL, None, "ui", None, None, "standard", None, "yolo", False, ()
    )
    async with asyncio.timeout(_CAPTURE_S):
        await manager.wait(run.id)
    if keeping.first is None:
        raise RuntimeError(f"the run of {goal!r} sent no request")
    return keeping.first


async def _asked(runtime: Runtime, ref: ModelRef, base: ChatRequest, case: ToolCase) -> CaseResult:
    history: list[Message] = prior_messages(case.prior)
    tokens: int = 0
    reply: ModelReply | None = None
    call: ToolCall | None = None
    for _turn in range(EXTRA_TURNS + 1):
        reply = await runtime.router.complete(ref, case_request(base, ref.model, history), _quiet, _ORIGIN)
        tokens += reply.usage.input_tokens
        call = first_work(reply)
        if call is not None or not reply.tool_calls:
            break
        history.extend(meta_answers(reply))
    # A model that only planned, every turn, did no work: it is no answer that ends the run.
    planned_only: bool = call is None and reply is not None and bool(reply.tool_calls)
    passed, why = (False, "it only planned") if planned_only else judge(case, call)
    return CaseResult(
        case_id=case.id,
        passed=passed,
        tool="" if call is None else call.name,
        arguments={} if call is None else call.arguments,
        why=why,
        input_tokens=tokens,
    )


async def play_cases(
    config: AppConfig,
    login_env: dict[str, str],
    keys: dict[str, str],
    model: str,
    cases: Sequence[ToolCase],
    repeats: int,
    on_result: Callable[[CaseResult], None] = lambda _result: None,
    answering: ChatProvider | None = None,
) -> list[CaseResult]:
    """Each of ``cases`` asked of ``model`` ``repeats`` times, in a throw-away home that holds ``keys``; ``config`` is
    an eval configuration (:func:`dotagent.evals.runner.eval_config`). A model that fails to answer fails the case,
    saying why, and the next goes on. ``answering``, when given, is registered as the provider of ``model`` (a test's
    scripted model: no model service is reached)."""
    ref: ModelRef = parse_model_ref(model)
    results: list[CaseResult] = []
    # The requests are captured first, in a runtime of their own: the scripted model that plays them is Ollama's
    # provider (``ollama:scripted``), which would answer for an Ollama model under test in the same runtime.
    bases: list[ChatRequest] = []
    with no_keychain(), scratch_home() as home, eval_environment(home, {}):
        capturing: Runtime = await open_runtime(config, login_env)
        manager: RunManager = RunManager(capturing)
        keeping: _Keeping = _Keeping()
        try:
            await capturing.router.register(_PROVIDER, keeping)
            for case in cases:
                bases.append(await _captured(capturing, manager, keeping, case.goal))
        finally:
            await manager.shutdown()
            await close_runtime(capturing)
    with no_keychain(), scratch_home() as home, eval_environment(home, keys):
        runtime: Runtime = await open_runtime(config, login_env)
        try:
            if answering is not None:
                await runtime.router.register(ref.provider, answering)
            for case, base in zip(cases, bases, strict=True):
                for _attempt in range(repeats):
                    try:
                        result: CaseResult = await _asked(runtime, ref, base, case)
                    except Exception as error:  # noqa: BLE001 - one case's failure is reported, the next goes on
                        # Its message alone: a provider's error carries the whole request in its context.
                        said: str = error.message if isinstance(error, DotError) else str(error)
                        result = CaseResult(
                            case_id=case.id, passed=False, tool="", arguments={}, why=f"the model failed: {said}",
                            input_tokens=0,
                        )  # fmt: skip
                    results.append(result)
                    on_result(result)
        finally:
            await close_runtime(runtime)
    return results
