"""A run whose model cannot be reached for a while, on a scripted model in a throw-away home: the run waits for the
connection and asks the same step again, the page told to drop what the failed try streamed, a stop ends the wait at
once, a model that stays out of reach fails the run once the wait is over, and a request the provider refuses or a
rate limit fails it at once; a run that an error fails keeps the error's message as its result and the error's context
with its end. No model is called.
"""

import asyncio
import time
from collections.abc import Sequence

import httpx
import pytest
import pytest_asyncio

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.core.errors import (
    ProviderAuthError,
    ProviderCreditError,
    ProviderError,
    ProviderHttpError,
    ProviderQuotaError,
    ProviderRateLimitError,
    ProviderTransportError,
    ProviderWindowError,
)
from dotagent.evals import EVAL_SOURCE
from dotagent.evals.runner import SCRIPTED_MODEL, eval_config, eval_environment, scratch_home, seed_workspace
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import FileSeed, ScriptedCall, ScriptedTurn
from dotagent.events import Subscription
from dotagent.llm.http import wrap_transport_error
from dotagent.llm.reach import OFFLINE
from dotagent.llm.router import ModelRef, ModelRouter, parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.store.models import EventRecord, RunRecord
from dotagent.tools.meta import FINISH_TOOL
from dotagent.tools.shell import read_login_env

pytestmark = pytest.mark.asyncio(loop_scope="module")

GOAL: str = "Read todo.txt and say what is on it."
TODO: FileSeed = FileSeed(path="todo.txt", content="Water the plants.\n", encoding="text")
TURNS: tuple[ScriptedTurn, ...] = (
    ScriptedTurn(text="", calls=(ScriptedCall(tool="read_file", arguments={"path": TODO.path}),)),
    ScriptedTurn(
        text="", calls=(ScriptedCall(tool=FINISH_TOOL, arguments={"answer": "Water the plants.", "success": True}),)
    ),
)
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_TIME_LIMIT_S: float = 60.0
_MAX_EVENTS: int = 1000
BROKEN: ProviderTransportError = wrap_transport_error(httpx.ConnectError("[Errno 51] Network is unreachable"), {})
OVERLOADED: ProviderHttpError = ProviderHttpError("the provider is overloaded", 529, {})
REFUSED: ProviderHttpError = ProviderHttpError("the request was refused", 400, {})
# A refusal with what a connector keeps of the request and the answer in the error's context.
REFUSED_WITH_CONTEXT: ProviderHttpError = ProviderHttpError(
    "provider answered HTTP 400: the request was refused",
    400,
    {"payload": "the request of the run", "response_body": "the answer of the provider"},
)
# A status the connector retries, but one that says the model cannot answer for a while, not that it is out of reach.
RATE_LIMITED: ProviderRateLimitError = ProviderRateLimitError("the rate limit is reached", 429, 5.0, {})
# A spent subscription window, as OpenCode Go words it (item 713), and a key the provider refuses, which no wait mends.
SPENT: ProviderWindowError = ProviderWindowError("provider answered HTTP 429: Go usage limit exceeded", 429, None, {})
# A monthly plan spent, which no wait within hours mends, and a quota whose provider named when it comes back.
MONTH_SPENT: ProviderQuotaError = ProviderQuotaError("You used all included Pro usage", 429, None, {})
NAMED: ProviderQuotaError = ProviderQuotaError("quota exceeded, try again in 0.1 s", 429, 0.1, {})
# Credits whose provider still named a time, and a quota that comes back only after the wait would be over.
CREDITS_NAMED: ProviderCreditError = ProviderCreditError("credit balance is too low", 402, 0.1, {}, None)
NEXT_MONTH: ProviderQuotaError = ProviderQuotaError("monthly limit reached", 429, 30 * 86_400.0, {})
KEY_REFUSED: ProviderAuthError = ProviderAuthError("provider answered HTTP 401: invalid key", 401, {})


class Unreachable:
    """The scripted model behind a connection that fails its first ``failures`` calls with ``error``."""

    def __init__(self, script: ScriptedProvider, failures: int, error: ProviderError) -> None:
        self._script: ScriptedProvider = script
        self._failures: int = failures
        self._error: ProviderError = error
        self.calls: int = 0

    @property
    def name(self) -> str:
        return self._script.name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.calls += 1
        if self.calls <= self._failures:
            raise self._error
        return await self._script.complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return await self._script.model_info(model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return await self._script.list_models()

    async def aclose(self) -> None:
        await self._script.aclose()


class Played:
    """A run that is over: its record, its events, the deltas it streamed (the store keeps none) and how long it
    took."""

    def __init__(
        self, record: RunRecord, events: Sequence[EventRecord], streamed: Sequence[EventRecord], seconds: float
    ) -> None:
        self.record: RunRecord = record
        self.events: tuple[EventRecord, ...] = tuple(events)
        self.streamed: tuple[EventRecord, ...] = tuple(streamed)
        self.seconds: float = seconds

    def kind(self, kind: str) -> list[EventRecord]:
        return [event for event in self.events if event.kind == kind]

    def deltas(self, kind: str) -> list[EventRecord]:
        return [event for event in self.streamed if event.kind == "delta" and event.data["kind"] == kind]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def login_env() -> dict[str, str]:
    return await read_login_env(load_default_config().shell.login_env_timeout_s, ())


def _config(wait_s: float, quota_wait_s: float, check_s: float) -> AppConfig:
    config: AppConfig = eval_config(load_default_config())
    waits: dict[str, float] = {"model_wait_s": wait_s, "quota_wait_s": quota_wait_s, "connection_check_s": check_s}
    return config.model_copy(update={"agent": config.agent.model_copy(update=waits)})


async def _until(subscription: Subscription, run_id: str, kind: str) -> EventRecord:
    """The next event of ``kind`` that run ``run_id`` publishes."""
    while True:
        event: EventRecord = await subscription.queue.get()
        if event.run_id == run_id and event.kind == kind:
            return event


def _left(subscription: Subscription, run_id: str) -> list[EventRecord]:
    """The events of run ``run_id`` still in the queue of ``subscription``."""
    events: list[EventRecord] = []
    while not subscription.queue.empty():
        event: EventRecord = subscription.queue.get_nowait()
        if event.run_id == run_id:
            events.append(event)
    return events


async def _play(
    login_env: dict[str, str],
    wait_s: float,
    model: Unreachable,
    stop_when_waiting: bool,
    quota_wait_s: float = 0.0,
    check_s: float = 45.0,
) -> Played:
    """``GOAL`` as an unattended run of ``model`` in a throw-away home; with ``stop_when_waiting`` the run is stopped
    as soon as it says it waits for its model."""
    with scratch_home() as home, eval_environment(home, {}):
        runtime: Runtime = await open_runtime(_config(wait_s, quota_wait_s, check_s), login_env)
        try:
            await runtime.router.register(_PROVIDER, model)
            await asyncio.to_thread(seed_workspace, runtime.workspace, [TODO])
            manager: RunManager = RunManager(runtime)
            try:
                async with runtime.bus.subscribe(None) as subscription:
                    started: float = time.monotonic()
                    run: RunRecord = await manager.start(
                        GOAL, "task", SCRIPTED_MODEL, None, EVAL_SOURCE, None, None, "standard", None, "ask", False, ()
                    )
                    async with asyncio.timeout(_TIME_LIMIT_S):
                        if stop_when_waiting:
                            await _until(subscription, run.id, "model_waiting")
                            started = time.monotonic()
                            manager.cancel(run.id)
                        record: RunRecord = await manager.wait(run.id)
                    seconds: float = time.monotonic() - started
                    streamed: list[EventRecord] = _left(subscription, run.id)
            finally:
                await manager.shutdown()
            events: list[EventRecord] = await runtime.store.list_events(run.id, 0, _MAX_EVENTS)
        finally:
            await close_runtime(runtime)
    return Played(record, events, streamed, seconds)


async def test_a_run_waits_for_its_model_to_be_reached_again_and_goes_on(login_env: dict[str, str]) -> None:
    model: Unreachable = Unreachable(ScriptedProvider(_PROVIDER, TURNS), 2, BROKEN)
    played: Played = await _play(login_env, 4.0, model, False)
    assert played.record.status == "done", played.record.result
    waiting: list[EventRecord] = played.kind("model_waiting")
    assert [event.data["step"] for event in waiting] == [1, 1]
    assert all(event.data["model"] == SCRIPTED_MODEL for event in waiting)
    assert waiting[0].data["error"] == "ConnectError: [Errno 51] Network is unreachable"
    # A short wait asks again every quarter of it.
    assert [event.data["retry_in_s"] for event in waiting] == [1.0, 1.0]
    back: list[EventRecord] = played.kind("model_back")
    assert len(back) == 1 and back[0].data["step"] == 1
    # The step was asked again, not counted again: two failed calls and the run's two steps.
    assert model.calls == 4
    assert played.record.steps == 2
    # Each time, the page was told to drop what the failed try streamed (item 246).
    assert [event.data["step"] for event in played.deltas("reset")] == [1, 1]


async def test_a_server_that_fails_with_a_status_retrying_gets_past_is_waited_for(login_env: dict[str, str]) -> None:
    played: Played = await _play(login_env, 4.0, Unreachable(ScriptedProvider(_PROVIDER, TURNS), 1, OVERLOADED), False)
    assert played.record.status == "done", played.record.result
    assert [event.data["error"] for event in played.kind("model_waiting")] == ["the provider is overloaded"]
    assert len(played.kind("model_back")) == 1


async def test_a_model_that_stays_out_of_reach_fails_the_run_once_the_wait_is_over(login_env: dict[str, str]) -> None:
    played: Played = await _play(login_env, 2.0, Unreachable(ScriptedProvider(_PROVIDER, TURNS), 1000, BROKEN), False)
    assert played.record.status == "failed"
    assert "could not be reached for 2 s: ConnectError: [Errno 51] Network is unreachable" in played.record.result
    # Asked again at least four times however short the wait, and never past it by more than a moment.
    assert len(played.kind("model_waiting")) >= 4
    assert 2.0 <= played.seconds < 6.0
    assert played.kind("model_back") == []


async def test_a_stop_ends_the_wait_at_once(login_env: dict[str, str]) -> None:
    played: Played = await _play(login_env, 600.0, Unreachable(ScriptedProvider(_PROVIDER, TURNS), 1000, BROKEN), True)
    assert played.record.status == "cancelled"
    assert played.seconds < 3.0


@pytest.mark.parametrize("error", [REFUSED, RATE_LIMITED], ids=["refused", "rate-limited"])
async def test_a_refused_or_rate_limited_request_fails_the_run_at_once(
    login_env: dict[str, str], error: ProviderHttpError
) -> None:
    played: Played = await _play(login_env, 600.0, Unreachable(ScriptedProvider(_PROVIDER, TURNS), 1000, error), False)
    assert played.record.status == "failed"
    assert error.message in played.record.result
    assert played.kind("model_waiting") == []
    assert played.seconds < 5.0


async def test_a_run_an_error_fails_shows_its_message_and_keeps_its_context_with_its_end(
    login_env: dict[str, str],
) -> None:
    model: Unreachable = Unreachable(ScriptedProvider(_PROVIDER, TURNS), 1000, REFUSED_WITH_CONTEXT)
    played: Played = await _play(login_env, 600.0, model, False)
    assert (played.record.status, played.record.result) == ("failed", REFUSED_WITH_CONTEXT.message)
    [finished] = played.kind("run_finished")
    assert finished.data["result"] == REFUSED_WITH_CONTEXT.message
    assert finished.data["error"] == {
        "message": REFUSED_WITH_CONTEXT.message,
        "context": {"payload": "the request of the run", "response_body": "the answer of the provider", "status": 400},
    }


@pytest.fixture
def quick_quota(monkeypatch: pytest.MonkeyPatch) -> None:
    """A spent quota asked again within a fraction of a second rather than minutes."""
    monkeypatch.setattr("dotagent.agent.loop._QUOTA_RETRY_S", 0.2)
    monkeypatch.setattr("dotagent.agent.loop._QUOTA_RETRY_LEAST_S", 0.1)


@pytest.mark.usefixtures("quick_quota")
async def test_a_run_waits_for_a_spent_quota_and_goes_on_once_it_is_back(login_env: dict[str, str]) -> None:
    """Item 710: a subscription whose window is spent, with no standby model to move to, made the run fail; it waits
    for the quota now, asks the same step again and goes on once the model answers."""
    model: Unreachable = Unreachable(ScriptedProvider(_PROVIDER, TURNS), 2, SPENT)
    played: Played = await _play(login_env, 600.0, model, False, quota_wait_s=600.0)
    assert played.record.status == "done", played.record.result
    waiting: list[EventRecord] = played.kind("model_waiting")
    assert [event.data["spent"] for event in waiting] == ["usage window spent", "usage window spent"]
    assert all(event.data["retry_in_s"] <= 0.2 for event in waiting)
    assert len(played.kind("model_back")) == 1


@pytest.mark.usefixtures("quick_quota")
async def test_a_quota_that_stays_spent_fails_the_run_with_the_providers_words_once_the_wait_is_over(
    login_env: dict[str, str],
) -> None:
    model: Unreachable = Unreachable(ScriptedProvider(_PROVIDER, TURNS), 1000, SPENT)
    played: Played = await _play(login_env, 600.0, model, False, quota_wait_s=1.0)
    assert played.record.status == "failed"
    assert SPENT.message in played.record.result
    assert len(played.kind("model_waiting")) >= 2
    assert 1.0 <= played.seconds < 5.0


@pytest.mark.usefixtures("quick_quota")
async def test_a_refused_key_never_waits_for_the_quota(login_env: dict[str, str]) -> None:
    model: Unreachable = Unreachable(ScriptedProvider(_PROVIDER, TURNS), 1000, KEY_REFUSED)
    played: Played = await _play(login_env, 600.0, model, False, quota_wait_s=600.0)
    assert played.record.status == "failed"
    assert played.kind("model_waiting") == []
    assert played.seconds < 5.0


class Failing(Unreachable):
    """The scripted model behind a connection that fails its first calls with ``errors``, one each, in order."""

    def __init__(self, script: ScriptedProvider, errors: Sequence[ProviderError]) -> None:
        super().__init__(script, len(errors), errors[0])
        self._errors: tuple[ProviderError, ...] = tuple(errors)

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        if self.calls < len(self._errors):
            self.calls += 1
            raise self._errors[self.calls - 1]
        self.calls += 1
        return await self._script.complete(request, sink)


@pytest.mark.usefixtures("quick_quota")
async def test_a_connection_that_breaks_while_the_quota_is_waited_for_does_not_end_the_wait(
    login_env: dict[str, str],
) -> None:
    """The code review of item 713: a broken connection during the wait for a spent quota (a Mac that wakes) was timed
    from the start of the whole wait against model_wait_s, and ended the run at once."""
    model: Failing = Failing(ScriptedProvider(_PROVIDER, TURNS), [SPENT, SPENT, SPENT, BROKEN])
    played: Played = await _play(login_env, 0.3, model, False, quota_wait_s=600.0)
    assert played.record.status == "done", played.record.result
    assert [event.data["spent"] for event in played.kind("model_waiting")] == [
        "usage window spent",
        "usage window spent",
        "usage window spent",
        None,
    ]
    assert played.seconds > 0.3


@pytest.mark.usefixtures("quick_quota")
@pytest.mark.parametrize(
    "error",
    [MONTH_SPENT, KEY_REFUSED, CREDITS_NAMED, NEXT_MONTH, NAMED],
    ids=["month-spent", "key-refused", "credits-named", "next-month", "quota-named-soon"],
)
async def test_a_quota_that_does_not_come_back_soon_ends_the_run_at_once(
    login_env: dict[str, str], error: ProviderError
) -> None:
    """The code reviews of item 713: only what comes back by itself is waited for, a rate limit and a subscription's
    usage window, not a monthly plan, credits or a spend limit, whatever their provider calls them or the time it
    names (a gateway may name one on every answer)."""
    played: Played = await _play(
        login_env, 600.0, Unreachable(ScriptedProvider(_PROVIDER, TURNS), 1000, error), False, quota_wait_s=600.0
    )
    assert played.record.status == "failed"
    assert played.kind("model_waiting") == []


class Network:
    """The connection to the model's server (item 769): down for its first ``down_for`` checks, up after."""

    def __init__(self, down_for: int) -> None:
        self.down_for: int = down_for
        self.checks: int = 0

    async def reaches(self, _ref: ModelRef) -> bool:
        self.checks += 1
        return self.checks > self.down_for


class Hanging(Unreachable):
    """The scripted model behind a call that sends nothing for ``silent_s`` (forever when ``None``) the first time it
    is asked: a network that dropped mid-call, or a slow model."""

    def __init__(self, script: ScriptedProvider, silent_s: float | None) -> None:
        super().__init__(script, 0, BROKEN)
        self._silent_s: float | None = silent_s

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.calls += 1
        if self.calls == 1:
            if self._silent_s is None:
                await asyncio.Event().wait()
            await asyncio.sleep(self._silent_s or 0.0)
        return await self._script.complete(request, sink)


_HOST: str = "model.example"


@pytest.fixture
def network(monkeypatch: pytest.MonkeyPatch) -> Network:
    """The model's server at an address of its own, whose connection is checked every few hundredths of a second."""
    checked: Network = Network(3)
    monkeypatch.setattr(ModelRouter, "address", lambda _router, _ref: f"https://{_HOST}/v1")
    monkeypatch.setattr(ModelRouter, "reaches", lambda _router, ref: checked.reaches(ref))
    monkeypatch.setattr("dotagent.agent.loop._CHECK_EVERY_S", 0.05)
    monkeypatch.setattr("dotagent.agent.loop._RECONNECT_EVERY_S", 0.05)
    return checked


async def test_a_call_whose_connection_is_gone_is_given_up_and_asked_again_as_soon_as_it_connects(
    login_env: dict[str, str], network: Network
) -> None:
    """The night of 2026-10-28: the network dropped while a step's call was out, and the call sent nothing for seven
    minutes, until model_call_timeout_s; then the run asked again on a schedule while the network was back."""
    model: Hanging = Hanging(ScriptedProvider(_PROVIDER, TURNS), None)
    played: Played = await _play(login_env, 600.0, model, False, check_s=0.2)
    assert played.record.status == "done", played.record.result
    [waiting] = played.kind("model_waiting")
    assert (waiting.data["offline"], waiting.data["retry_in_s"]) == (_HOST, None)
    assert waiting.data["error"] == f"cannot connect to {_HOST}"
    [reconnected] = played.kind("model_reconnected")
    assert reconnected.data["host"] == _HOST and reconnected.data["step"] == 1
    # Offline since the call fell silent, not since it was given up.
    assert reconnected.data["offline_s"] >= 0.2
    assert len(played.kind("model_back")) == 1
    assert model.calls == 3 and network.checks == 4
    # Given up a moment after it fell silent, not at model_call_timeout_s.
    assert waiting.ts - played.kind("run_started")[0].ts < 10.0


async def test_a_slow_model_whose_server_is_there_is_waited_for(login_env: dict[str, str], network: Network) -> None:
    """Its server is there, but one check fails (a lookup that hiccups): one failed check does not cut the call (the
    pre-push review of item 769)."""
    network.down_for = 1
    model: Hanging = Hanging(ScriptedProvider(_PROVIDER, TURNS), 0.6)
    played: Played = await _play(login_env, 600.0, model, False, check_s=0.1)
    assert played.record.status == "done", played.record.result
    assert played.kind("model_waiting") == []
    assert model.calls == 2 and network.checks >= 3


async def test_a_call_that_could_not_connect_waits_for_the_connection_not_for_a_schedule(
    login_env: dict[str, str], network: Network
) -> None:
    """A connector that found the server out of reach says so at once (item 769); the run asks again as soon as it
    connects, whatever the schedule of a model that cannot answer would be."""
    offline: ProviderTransportError = ProviderTransportError(f"cannot connect to {_HOST}", {OFFLINE: _HOST})
    played: Played = await _play(login_env, 600.0, Unreachable(ScriptedProvider(_PROVIDER, TURNS), 1, offline), False)
    assert played.record.status == "done", played.record.result
    [waiting] = played.kind("model_waiting")
    assert (waiting.data["offline"], waiting.data["retry_in_s"]) == (_HOST, None)
    [reconnected] = played.kind("model_reconnected")
    # Asked again as soon as it connected: three checks found it down, 0.05 s apart, far from the first 10 s.
    assert network.checks == 4 and reconnected.data["offline_s"] < 5.0
