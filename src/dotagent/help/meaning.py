"""Help found by meaning (item 169), as well as by words (:mod:`dotagent.help.rank`): with the embeddings the notes use
(``[memory] embed_provider`` and ``embed_model``; bge-m3 reads Turkish as well as English), a need in one language finds
help described in another. The embedding of each text that help is ranked by is made once, kept by the process up to a
bound (``_KEPT_TEXTS``) and in the store across its restarts (item 404): the skills, tools and applications of a Mac
change seldom, and embedding them all at every search, or at every start, took seconds to minutes. Texts are embedded
``_BATCH`` at a time: one call with hundreds of them broke Ollama's model runner. A search that finds more than
``_COLD_TEXTS`` of its texts unembedded does not wait: it starts the embedding in the background and raises
:class:`HelpVectorsCold`, so the help is ranked by words this once."""

import asyncio
import contextlib
import time
from collections.abc import Sequence
from typing import Protocol

import structlog

from dotagent.core.errors import ProviderError, StoreError
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
# A text no search asked for in this long is dropped from the keeper at the next warming: a skill, tool or application
# described otherwise since, or gone.
_KEEP_S: float = 30 * 86400


class HelpVectorsCold(Exception):
    """The texts of a search are not embedded yet; they are being embedded in the background."""

    def __init__(self, missing: int) -> None:
        super().__init__(f"{missing} texts are not embedded yet")
        self.missing: int = missing


class VectorKeeper(Protocol):
    """Where the embeddings of help texts outlive the process: the store."""

    async def help_vectors(self, model: str) -> dict[str, tuple[float, ...]]: ...

    async def help_vectors_of(self, model: str, texts: Sequence[str]) -> dict[str, tuple[float, ...]]: ...

    async def add_help_vectors(
        self, model: str, vectors: Sequence[tuple[str, Sequence[float]]], now: float
    ) -> None: ...

    async def touch_help_vectors(self, model: str, texts: Sequence[str], now: float) -> None: ...

    async def prune_help_vectors(self, model: str, before: float) -> int: ...


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
        self._loading: asyncio.Lock = asyncio.Lock()
        # Whether the vectors kept here were cut to ``_KEPT_TEXTS`` since the keeper's were read: a text missing here
        # may be with the keeper still, and is read back before it is embedded again.
        self._trimmed: bool = False
        # The texts the keeper was told are in use since this process started, so each is told once.
        self._touched: set[str] = set()
        self._warming: asyncio.Task[None] | None = None
        # The texts asked for while a warming runs: it takes them next, before it ends.
        self._queued: set[str] = set()

    async def _load(self) -> None:
        """The vectors the keeper holds for this embedding model, once; a second caller waits for the first."""
        async with self._loading:
            if self._loaded:
                return
            if self._keeper is not None:
                self._kept.update(await self._keeper.help_vectors(self._embedder.model))
            self._loaded = True

    async def _embed(self, texts: Sequence[str]) -> dict[str, Vector]:
        """Embed ``texts``, ``_BATCH`` at a time, and keep the vectors here and with the keeper; returns them, since
        the ones kept here may be cut by a search that runs meanwhile."""
        made: dict[str, Vector] = {}
        for start in range(0, len(texts), _BATCH):
            chunk: list[str] = list(texts[start : start + _BATCH])
            vectors: list[Vector] = await self._embedder.embed(chunk)
            made.update(zip(chunk, vectors, strict=True))
            self._kept.update(zip(chunk, vectors, strict=True))
            if self._keeper is not None:
                await self._keeper.add_help_vectors(
                    self._embedder.model, list(zip(chunk, vectors, strict=True)), time.time()
                )
            self._touched.update(chunk)
        return made

    async def _touch(self, texts: Sequence[str]) -> None:
        """Tell the keeper the kept texts of ``texts`` are in use, once each while this process runs, so that
        :meth:`prune` keeps them."""
        fresh: list[str] = list(dict.fromkeys(t for t in texts if t in self._kept and t not in self._touched))
        if fresh and self._keeper is not None:
            await self._keeper.touch_help_vectors(self._embedder.model, fresh, time.time())
        self._touched.update(fresh)

    async def _missing(self, texts: Sequence[str]) -> list[str]:
        """The texts of ``texts`` with no vector here, those the keeper still has read back first."""
        missing: list[str] = list(dict.fromkeys(text for text in texts if text not in self._kept))
        if missing and self._trimmed and self._keeper is not None:
            self._kept.update(await self._keeper.help_vectors_of(self._embedder.model, missing))
            missing = [text for text in missing if text not in self._kept]
        return missing

    async def prepare(self, texts: Sequence[str]) -> int:
        """Embed the texts of ``texts`` not embedded yet, so that a search finds them ready. Returns how many were."""
        await self._load()
        missing: list[str] = await self._missing(texts)
        await self._embed(missing)
        await self._touch(texts)
        return len(missing)

    async def prune(self) -> int:
        """Drop from the keeper the texts no search asked for in ``_KEEP_S``, and another embedding model's. Returns
        how many went."""
        if self._keeper is None:
            return 0
        return await self._keeper.prune_help_vectors(self._embedder.model, time.time() - _KEEP_S)

    def warm(self, texts: Sequence[str]) -> asyncio.Task[None]:
        """Start :meth:`prepare` and :meth:`prune` in the background, unless a warming is running: then ``texts``
        are queued for it to take next, and that warming is returned, so the work is done once. A failure is logged,
        and the next search starts it again."""
        if self._warming is not None and not self._warming.done():
            self._queued.update(texts)
            return self._warming

        async def run() -> None:
            try:
                made: int = await self.prepare(texts)
                while self._queued:
                    queued: list[str] = sorted(self._queued)
                    self._queued.clear()
                    made += await self.prepare(queued)
                pruned: int = await self.prune()
                log.info("help_vectors_warmed", texts=len(texts), embedded=made, pruned=pruned)
            except (ProviderError, StoreError) as error:
                self._queued.clear()
                log.warning("help_vectors_warming_failed", error_type=type(error).__name__, error=str(error))

        self._warming = asyncio.create_task(run(), name="help-vectors")
        return self._warming

    async def aclose(self) -> None:
        """End a warming still under way, as the runtime closes: nothing is written to a closed store."""
        warming: asyncio.Task[None] | None = self._warming
        self._warming = None
        if warming is not None and not warming.done():
            warming.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await warming

    async def closest(self, need: str, texts: Sequence[str]) -> list[int]:
        """The places in ``texts`` of those at least ``minimum`` close to ``need`` in meaning, the closest first. The
        texts not seen before are embedded first, then the need; with more than ``_COLD_TEXTS`` of them unembedded the
        search raises :class:`HelpVectorsCold` and embeds them in the background instead. An embedding service that
        fails raises its error."""
        await self._load()
        missing: list[str] = await self._missing(texts)
        if len(missing) > _COLD_TEXTS:
            self.warm(texts)
            raise HelpVectorsCold(len(missing))
        made: dict[str, Vector] = await self._embed(missing)
        # The vectors of this search, held here: a search that runs meanwhile may cut the ones kept by the process.
        vectors: dict[str, Vector] = {}
        for text in texts:
            kept: Vector | None = self._kept.get(text, made.get(text))
            if kept is not None:
                vectors[text] = kept
        cut: list[str] = [text for text in dict.fromkeys(texts) if text not in vectors]
        if cut:
            vectors.update(await self._embed(cut))
        await self._touch(texts)
        [vector] = await self._embedder.embed([need[:_NEED_CHARS]])
        if len(self._kept) > _KEPT_TEXTS:
            self._kept = {text: vectors[text] for text in texts}
            self._trimmed = True
        close: list[Similar] = most_similar(
            vector, ((str(index), vectors[text]) for index, text in enumerate(texts)), self._minimum, len(texts)
        )
        return [int(item.id) for item in close]
