"""How the router makes its connectors (item 558): one at a time for each provider, and not for all of them. Finding a
key can take seconds (a login Keychain that does not answer, up to 15 s), and every call of every run asks the router
for its provider's connector: a connector that exists is returned at once, and one being made holds back the calls that
wait for that provider alone."""

import asyncio

import pytest

from dotagent.config import load_default_config
from dotagent.core.errors import ConfigError
from dotagent.llm import router as router_module
from dotagent.llm.calls import CallOrigin, ModelCall
from dotagent.llm.router import ModelRef, ModelRouter
from dotagent.llm.types import ChatProvider, ChatRequest, DeltaSink, ModelInfo, ModelReply, StreamDelta, Usage

_SOON_S: float = 1.0
_LONG_ENOUGH_S: float = 0.05


class _Connector:
    """A connector that does nothing but remember that it was closed."""

    def __init__(self, name: str) -> None:
        self._name: str = name
        self.closed: bool = False
        self.shut: asyncio.Event = asyncio.Event()

    @property
    def name(self) -> str:
        return self._name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        raise NotImplementedError

    async def model_info(self, model: str) -> ModelInfo:
        raise NotImplementedError

    async def list_models(self) -> tuple[ModelInfo, ...]:
        raise NotImplementedError

    async def aclose(self) -> None:
        self.closed = True
        self.shut.set()


class _Made:
    """What the router makes: a connector for each name at once, but for ``slow``, which waits for ``release``."""

    def __init__(self, slow: str) -> None:
        self.slow: str = slow
        self.release: asyncio.Event = asyncio.Event()
        self.started: asyncio.Event = asyncio.Event()
        self.made: list[_Connector] = []

    async def __call__(self, name: str) -> ChatProvider:
        connector: _Connector = _Connector(name)
        self.made.append(connector)
        if name == self.slow:
            self.started.set()
            await self.release.wait()
        return connector


async def _record(call: ModelCall) -> None:
    return None


@pytest.fixture
def made() -> _Made:
    return _Made(slow="openai")


@pytest.fixture
def router(made: _Made, monkeypatch: pytest.MonkeyPatch) -> ModelRouter:
    built: ModelRouter = ModelRouter(load_default_config(), _record, {})
    monkeypatch.setattr(built, "_create", made)
    return built


async def test_a_connector_being_made_holds_back_no_other_provider(router: ModelRouter, made: _Made) -> None:
    ollama: ChatProvider = await router.provider("ollama")
    slow: asyncio.Task[ChatProvider] = asyncio.create_task(router.provider("openai"))
    await asyncio.wait_for(made.started.wait(), _SOON_S)
    # While openai's key is being found, the connector that exists is returned and another provider's is made.
    assert await asyncio.wait_for(router.provider("ollama"), _SOON_S) is ollama
    assert (await asyncio.wait_for(router.provider("openrouter"), _SOON_S)).name == "openrouter"
    assert not slow.done()
    made.release.set()
    assert await asyncio.wait_for(slow, _SOON_S) is await router.provider("openai")


async def test_calls_that_wait_for_the_same_provider_share_one_connector(router: ModelRouter, made: _Made) -> None:
    waiting: list[asyncio.Task[ChatProvider]] = [asyncio.create_task(router.provider("openai")) for _ in range(3)]
    await asyncio.wait_for(made.started.wait(), _SOON_S)
    await asyncio.sleep(_LONG_ENOUGH_S)
    made.release.set()
    connectors: list[ChatProvider] = [await asyncio.wait_for(task, _SOON_S) for task in waiting]
    assert connectors[0] is connectors[1] is connectors[2]
    assert [connector.name for connector in made.made] == ["openai"]


async def test_a_provider_retired_while_its_connector_is_made_gets_a_new_one(router: ModelRouter, made: _Made) -> None:
    """The key saved while the old one was being read: the connector made with the old key is not kept."""
    first: asyncio.Task[ChatProvider] = asyncio.create_task(router.provider("openai"))
    await asyncio.wait_for(made.started.wait(), _SOON_S)
    retiring: asyncio.Task[None] = asyncio.create_task(router.retire("openai"))
    await asyncio.sleep(_LONG_ENOUGH_S)
    assert not retiring.done()
    made.release.set()
    old: ChatProvider = await asyncio.wait_for(first, _SOON_S)
    await asyncio.wait_for(retiring, _SOON_S)
    assert await asyncio.wait_for(router.provider("openai"), _SOON_S) is not old
    assert len(made.made) == 2


async def test_a_connector_made_while_the_router_closes_is_closed_not_left_behind(
    router: ModelRouter, made: _Made
) -> None:
    slow: asyncio.Task[ChatProvider] = asyncio.create_task(router.provider("openai"))
    await asyncio.wait_for(made.started.wait(), _SOON_S)
    await router.aclose()
    made.release.set()
    with pytest.raises(ConfigError, match="closed"):
        await asyncio.wait_for(slow, _SOON_S)
    assert made.made[0].closed


async def test_an_unknown_provider_is_refused(router: ModelRouter) -> None:
    with pytest.raises(ConfigError, match="unknown provider"):
        await router.provider("nowhere")


class _Serving(_Connector):
    """A connector whose call lasts until ``release`` is set, and answers when it is."""

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.release: asyncio.Event = asyncio.Event()
        self.started: asyncio.Event = asyncio.Event()

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        self.started.set()
        await self.release.wait()
        return REPLY


_REQUEST: ChatRequest = ChatRequest(
    model="fake",
    messages=(),
    tools=(),
    temperature=None,
    max_output_tokens=None,
    effort="medium",
    speed="standard",
    session_key="thr_x:goal",
    cache_mark=None,
    prompt_reuse="single",
    window=None,
)
REPLY: ModelReply = ModelReply(
    model="fake",
    text="ok",
    thinking="",
    tool_calls=(),
    usage=Usage(input_tokens=1, output_tokens=1, cached_tokens=0),
    finish="stop",
    raw_finish="stop",
    raw=(),
)
_REF: ModelRef = ModelRef(provider="openrouter", model="fake")
_ORIGIN: CallOrigin = CallOrigin(run_id=None, purpose="goal_check")


async def _nothing(delta: StreamDelta) -> None:
    return None


async def _closed_within(connector: _Connector, seconds: float) -> bool:
    await asyncio.wait_for(connector.shut.wait(), seconds)
    return True


async def test_a_connector_a_key_change_retired_is_closed_after_its_call_ended_and_not_before(
    router: ModelRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Item 1314: retired connectors waited in a list until the daemon stopped, one for each key change."""
    monkeypatch.setattr(router_module, "_RETIRED_GRACE_S", 0.05)
    old: _Serving = _Serving("openrouter")
    router._providers["openrouter"] = old
    calling: asyncio.Task[ModelReply] = asyncio.create_task(router.complete(_REF, _REQUEST, _nothing, _ORIGIN))
    await asyncio.wait_for(old.started.wait(), _SOON_S)
    await router.retire("openrouter")
    # The call that the old connector is serving is not cut, however long it takes.
    await asyncio.sleep(0.3)
    assert not old.closed and router._retired == [old]
    # A call made now goes to a connector made with the key in use now.
    fresh: ChatProvider = await router.provider("openrouter")
    assert fresh is not old
    old.release.set()
    assert await asyncio.wait_for(calling, _SOON_S) == REPLY
    assert await _closed_within(old, _SOON_S)
    await asyncio.wait_for(asyncio.gather(*router._closers), _SOON_S)
    await asyncio.sleep(0)
    assert router._retired == [] and not router._closers
    assert not getattr(fresh, "closed", True), "the one in service is left open"


async def test_a_retired_connector_that_serves_nothing_is_closed_after_the_grace(
    router: ModelRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(router_module, "_RETIRED_GRACE_S", 0.05)
    idle: _Connector = _Connector("openrouter")
    router._providers["openrouter"] = idle
    await router.retire("openrouter")
    assert not idle.closed
    assert await _closed_within(idle, _SOON_S)
    assert router._retired == []


async def test_a_key_changed_twice_closes_each_connector_once(
    router: ModelRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(router_module, "_RETIRED_GRACE_S", 0.05)
    first: _Connector = _Connector("openrouter")
    router._providers["openrouter"] = first
    await router.retire("openrouter")
    second: ChatProvider = await router.provider("openrouter")
    await router.retire("openrouter")
    assert await _closed_within(first, _SOON_S) and await _closed_within(second, _SOON_S)  # type: ignore[arg-type]
    third: ChatProvider = await router.provider("openrouter")
    assert third is not first and third is not second
    await router.aclose()
    assert third.closed is True  # type: ignore[attr-defined]


async def test_aclose_is_the_final_sweep_of_a_retired_connector_still_in_its_grace(router: ModelRouter) -> None:
    waiting: _Connector = _Connector("openrouter")
    router._providers["openrouter"] = waiting
    await router.retire("openrouter")
    assert not waiting.closed and router._retired == [waiting]
    await asyncio.wait_for(router.aclose(), _SOON_S)
    assert waiting.closed and router._retired == [] and not router._closers


async def test_aclose_closes_a_connector_whose_call_is_still_going(router: ModelRouter) -> None:
    serving: _Serving = _Serving("openrouter")
    router._providers["openrouter"] = serving
    calling: asyncio.Task[ModelReply] = asyncio.create_task(router.complete(_REF, _REQUEST, _nothing, _ORIGIN))
    await asyncio.wait_for(serving.started.wait(), _SOON_S)
    await router.retire("openrouter")
    await asyncio.wait_for(router.aclose(), _SOON_S)
    assert serving.closed
    serving.release.set()
    assert await asyncio.wait_for(calling, _SOON_S) == REPLY
    assert router._leases == {} and not router._closers
