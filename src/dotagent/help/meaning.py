"""Help found by meaning (item 169), as well as by words (:mod:`dotagent.help.rank`): with the embeddings the notes use
(``[memory] embed_provider`` and ``embed_model``; bge-m3 reads Turkish as well as English), a need in one language finds
help described in another. The embedding of each text that help is ranked by is made once, kept by the process up to a
bound (``_KEPT_TEXTS``) and in the store across its restarts (item 404): the skills, tools and applications of a Mac
change seldom, and embedding them all at every search, or at every start, took seconds to minutes. Texts are embedded
``_BATCH`` at a time: one call with hundreds of them broke Ollama's model runner. A search that finds more than
``_COLD_TEXTS`` of its texts unembedded does not wait: it starts the embedding in the background and raises
:class:`HelpVectorsCold`, so the help is ranked by words this once."""

import asyncio
from collections.abc import Sequence
from typing import Protocol

import structlog

from dotagent.memory.embedder import Embedder
from dotagent.memory.ranking import Similar, Vector, most_similar

log = structlog.get_logger(__name__)

# The most texts kept by the process; past it, only the texts of the latest search are.
_KEPT_TEXTS: int = 4096
# How many texts one embedding call takes.
_BATCH: int = 32
# The most characters of a need that are embedded, as for a note's search.
_NEED_CHARS: int = 1500
# Unembedded texts past which a search does not wait for them.
_COLD_TEXTS: int = 64


class HelpVectorsCold(Exception):
    """The texts of a search are not embedded yet; they are being embedded in the background."""

    def __init__(self, missing: int) -> None:
        super().__init__(f"{missing} texts are not embedded yet")
        self.missing: int = missing


class VectorKeeper(Protocol):
    """Where the embeddings of help texts outlive the process: the store."""

    async def help_vectors(self, model: str) -> dict[str, tuple[float, ...]]: ...

    async def add_help_vectors(self, model: str, vectors: Sequence[tuple[str, Sequence[float]]]) -> None: ...


class HelpVectors:
    """The embeddings of the texts that help is ranked by, kept by text. ``minimum`` is the weakest cosine similarity
    that counts as close: ``[memory] min_similarity``, calibrated for the embedding model. ``keeper`` keeps them across
    restarts; ``None`` keeps them in the process alone."""

    def __init__(self, embedder: Embedder, minimum: float, keeper: VectorKeeper | None) -> None:
        self._embedder: Embedder = embedder
        self._minimum: float = minimum
        self._keeper: VectorKeeper | None = keeper
        self._kept: dict[str, Vector] = {}
        self._loaded: bool = False
        self._warming: asyncio.Task[None] | None = None

    async def _load(self) -> None:
        """The vectors the keeper holds for this embedding model, once."""
        if self._loaded:
            return
        self._loaded = True
        if self._keeper is not None:
            self._kept.update(await self._keeper.help_vectors(self._embedder.model))

    async def _embed(self, texts: Sequence[str]) -> None:
        """Embed ``texts``, ``_BATCH`` at a time, and keep the vectors here and with the keeper."""
        for start in range(0, len(texts), _BATCH):
            chunk: list[str] = list(texts[start : start + _BATCH])
            vectors: list[Vector] = await self._embedder.embed(chunk)
            self._kept.update(zip(chunk, vectors, strict=True))
            if self._keeper is not None:
                await self._keeper.add_help_vectors(self._embedder.model, list(zip(chunk, vectors, strict=True)))

    def _missing(self, texts: Sequence[str]) -> list[str]:
        return list(dict.fromkeys(text for text in texts if text not in self._kept))

    async def prepare(self, texts: Sequence[str]) -> int:
        """Embed the texts of ``texts`` not embedded yet, so that a search finds them ready. Returns how many were."""
        await self._load()
        missing: list[str] = self._missing(texts)
        await self._embed(missing)
        return len(missing)

    def warm(self, texts: Sequence[str]) -> None:
        """Start :meth:`prepare` in the background, unless it is running; a failure is logged, and the next search
        starts it again."""
        if self._warming is not None and not self._warming.done():
            return

        async def run() -> None:
            try:
                made: int = await self.prepare(texts)
                log.info("help_vectors_warmed", texts=len(texts), embedded=made)
            except Exception as error:
                log.warning("help_vectors_warming_failed", error_type=type(error).__name__, error=str(error))

        self._warming = asyncio.create_task(run(), name="help-vectors")

    async def closest(self, need: str, texts: Sequence[str]) -> list[int]:
        """The places in ``texts`` of those at least ``minimum`` close to ``need`` in meaning, the closest first. The
        texts not seen before are embedded first, then the need; with more than ``_COLD_TEXTS`` of them unembedded the
        search raises :class:`HelpVectorsCold` and embeds them in the background instead. An embedding service that
        fails raises its error."""
        await self._load()
        missing: list[str] = self._missing(texts)
        if len(missing) > _COLD_TEXTS:
            self.warm(texts)
            raise HelpVectorsCold(len(missing))
        await self._embed(missing)
        [vector] = await self._embedder.embed([need[:_NEED_CHARS]])
        if len(self._kept) > _KEPT_TEXTS:
            self._kept = {text: self._kept[text] for text in texts}
        close: list[Similar] = most_similar(
            vector, ((str(index), self._kept[text]) for index, text in enumerate(texts)), self._minimum, len(texts)
        )
        return [int(item.id) for item in close]
