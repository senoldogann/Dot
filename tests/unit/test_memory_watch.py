"""The owner is told once when the embedding service stops answering and memory goes on by words (item 1308), and told
again only after the service answered and failed anew. A fake bus keeps the tells; the service is a stand-in."""

from collections.abc import Sequence

import pytest

from dotagent.core.errors import ProviderHttpError, ProviderTransportError
from dotagent.core.types import JsonObject
from dotagent.memory.ranking import Vector
from dotagent.memory.watch import DEGRADED_TITLE, MEMORY_ROUTE, WatchedEmbedder
from dotagent.notify.owner import OWNER_TOLD_EVENT

NOT_INSTALLED: ProviderHttpError = ProviderHttpError("provider answered HTTP 404: model 'bge-m3' not found", 404, {})


class _Service:
    """One vector per text while up, ``error`` while down."""

    def __init__(self) -> None:
        self.error: Exception | None = NOT_INSTALLED
        self.closed: bool = False

    @property
    def model(self) -> str:
        return "bge-m3"

    async def embed(self, texts: Sequence[str]) -> list[Vector]:
        if self.error is not None:
            raise self.error
        return [(1.0, 0.0) for _ in texts]

    async def aclose(self) -> None:
        self.closed = True


class _Bus:
    def __init__(self) -> None:
        self.told: list[JsonObject] = []
        self.fail: bool = False

    async def emit(self, run_id: str, kind: str, data: JsonObject, persist: bool) -> None:
        assert kind == OWNER_TOLD_EVENT and persist is False
        if self.fail:
            raise OSError("the bus is not there")
        self.told.append(data)


def _watched(service: _Service, bus: _Bus) -> WatchedEmbedder:
    return WatchedEmbedder(service, bus)  # type: ignore[arg-type]


async def test_the_first_failure_tells_the_owner_once_and_the_failure_still_reaches_the_caller() -> None:
    service, bus = _Service(), _Bus()
    watched: WatchedEmbedder = _watched(service, bus)
    for _ in range(3):
        with pytest.raises(ProviderHttpError):
            await watched.embed(["a note"])
    assert len(bus.told) == 1 and watched.down
    tell: JsonObject = bus.told[0]
    assert tell["title"] == DEGRADED_TITLE and tell["url"] == MEMORY_ROUTE
    assert "bge-m3" in str(tell["text"]) and "word" in str(tell["text"])


async def test_the_tell_is_made_again_only_after_the_service_answered_and_failed_anew() -> None:
    service, bus = _Service(), _Bus()
    watched: WatchedEmbedder = _watched(service, bus)
    with pytest.raises(ProviderHttpError):
        await watched.embed(["a note"])
    service.error = None
    assert await watched.embed(["a note"]) == [(1.0, 0.0)] and not watched.down
    assert await watched.embed([]) == []
    service.error = ProviderTransportError("the embedder is down", {})
    with pytest.raises(ProviderTransportError):
        await watched.embed(["a note"])
    assert len(bus.told) == 2


async def test_a_service_that_works_tells_nothing_and_an_empty_call_does_not_re_arm_the_tell() -> None:
    service, bus = _Service(), _Bus()
    service.error = None
    watched: WatchedEmbedder = _watched(service, bus)
    assert await watched.embed(["one", "two"]) == [(1.0, 0.0), (1.0, 0.0)]
    assert bus.told == [] and not watched.down
    service.error = NOT_INSTALLED
    with pytest.raises(ProviderHttpError):
        await watched.embed(["x"])
    assert await _empty(watched, service) == []
    with pytest.raises(ProviderHttpError):
        await watched.embed(["x"])
    assert len(bus.told) == 1


async def test_a_tell_that_cannot_be_made_is_tried_again_at_the_next_failure_and_never_hides_the_failure() -> None:
    service, bus = _Service(), _Bus()
    bus.fail = True
    watched: WatchedEmbedder = _watched(service, bus)
    with pytest.raises(ProviderHttpError):
        await watched.embed(["a note"])
    assert not watched.down and bus.told == []
    bus.fail = False
    with pytest.raises(ProviderHttpError):
        await watched.embed(["a note"])
    assert len(bus.told) == 1


async def test_the_watched_embedder_is_the_service_it_wraps() -> None:
    service: _Service = _Service()
    watched: WatchedEmbedder = _watched(service, _Bus())
    assert watched.model == "bge-m3"
    await watched.aclose()
    assert service.closed


async def _empty(watched: WatchedEmbedder, service: _Service) -> list[Vector]:
    """An empty call, which the service answers without being reached: it says nothing of whether the service works."""
    saved: Exception | None = service.error
    service.error = None
    try:
        return await watched.embed([])
    finally:
        service.error = saved
