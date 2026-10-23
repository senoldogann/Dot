"""How the router makes its connectors (item 558): one at a time for each provider, and not for all of them. Finding a
key can take seconds (a login Keychain that does not answer, up to 15 s), and every call of every run asks the router
for its provider's connector: a connector that exists is returned at once, and one being made holds back the calls that
wait for that provider alone."""

import asyncio

import pytest

from dotagent.config import load_default_config
from dotagent.core.errors import ConfigError
from dotagent.llm.calls import ModelCall
from dotagent.llm.router import ModelRouter
from dotagent.llm.types import ChatProvider, ChatRequest, DeltaSink, ModelInfo, ModelReply

_SOON_S: float = 1.0
_LONG_ENOUGH_S: float = 0.05


class _Connector:
    """A connector that does nothing but remember that it was closed."""

    def __init__(self, name: str) -> None:
        self._name: str = name
        self.closed: bool = False

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
