"""Telling the owner, once, that the embedding service stopped answering (item 1308).

Notes are saved, recalled and checked by meaning through the embedding service, and when it stops answering (Ollama
without ``bge-m3``, a server that is down, a key that is refused) every one of those calls goes on by the words of the
notes alone, each with a line in the log. Nothing else says that memory has become less than it was, so
:class:`WatchedEmbedder` tells the owner the first time a call fails after the service worked, and again only after it
has answered once and failed anew."""

from collections.abc import Sequence

from dotagent.core.errors import DotError, ProviderError
from dotagent.core.logs import get_logger
from dotagent.events import EventBus
from dotagent.memory.embedder import Embedder
from dotagent.memory.ranking import Vector
from dotagent.notify.owner import tell_owner

log = get_logger(__name__)

DEGRADED_TITLE: str = "Memory is on word search"
# The page of the notes, where the owner reads what is saved.
MEMORY_ROUTE: str = "#/memory"
_ERROR_CHARS: int = 160


def degraded_text(model: str, error: ProviderError) -> str:
    """What the tell says: which embedding model does not answer, why in the provider's words, and what that means."""
    return (
        f"The embedding model {model} is not answering ({error.message[:_ERROR_CHARS]}), so Dot finds and compares "
        "notes by their words until it does. Nothing is lost; `dotx doctor` says what to fix."
    )


class WatchedEmbedder:
    """``inner``, with a note to the owner the first time it fails after it worked (or since Dot started)."""

    def __init__(self, inner: Embedder, bus: EventBus) -> None:
        self._inner: Embedder = inner
        self._bus: EventBus = bus
        self._down: bool = False

    @property
    def model(self) -> str:
        return self._inner.model

    @property
    def down(self) -> bool:
        """Whether the last call of the service failed and none has worked since."""
        return self._down

    async def embed(self, texts: Sequence[str]) -> list[Vector]:
        if not texts:
            return await self._inner.embed(texts)
        try:
            vectors: list[Vector] = await self._inner.embed(texts)
        except ProviderError as error:
            await self._failed(error)
            raise
        if self._down:
            self._down = False
            log.info("memory_embeddings_back", model=self._inner.model)
        return vectors

    async def _failed(self, error: ProviderError) -> None:
        if self._down:
            return
        # Marked before the tell, so that two calls failing together tell once; a tell that fails takes the mark back.
        self._down = True
        log.warning(
            "memory_degraded",
            model=self._inner.model,
            error_type=type(error).__name__,
            error=error.message,
        )
        try:
            await tell_owner(
                self._bus, DEGRADED_TITLE, degraded_text(self._inner.model, error), MEMORY_ROUTE, "system", ""
            )
        except (DotError, OSError) as failure:
            self._down = False
            log.warning("memory_degraded_not_told", error=str(failure))

    async def aclose(self) -> None:
        await self._inner.aclose()
