"""Help found by meaning (item 169), as well as by words (:mod:`dotagent.help.rank`): with the embeddings the notes use
(``[memory] embed_provider`` and ``embed_model``; bge-m3 reads Turkish as well as English), a need in one language finds
help described in another. The embedding of each text that help is ranked by is made once and kept by the process, up
to a bound (``_KEPT_TEXTS``): the skills, tools and applications of a Mac change seldom, and embedding them all at every
search takes seconds."""

from collections.abc import Sequence

from dotagent.memory.embedder import Embedder
from dotagent.memory.ranking import Similar, Vector, most_similar

# The most texts kept; past it, only the texts of the latest search are.
_KEPT_TEXTS: int = 4096


class HelpVectors:
    """The embeddings of the texts that help is ranked by, kept by text. ``minimum`` is the weakest cosine similarity
    that counts as close: ``[memory] min_similarity``, calibrated for the embedding model."""

    def __init__(self, embedder: Embedder, minimum: float) -> None:
        self._embedder: Embedder = embedder
        self._minimum: float = minimum
        self._kept: dict[str, Vector] = {}

    async def closest(self, need: str, texts: Sequence[str]) -> list[int]:
        """The places in ``texts`` of those at least ``minimum`` close to ``need`` in meaning, the closest first. The
        need and the texts not seen before are embedded in one call; an embedding service that fails raises its
        error."""
        missing: list[str] = list(dict.fromkeys(text for text in texts if text not in self._kept))
        vectors: list[Vector] = await self._embedder.embed([need, *missing])
        self._kept.update(zip(missing, vectors[1:], strict=True))
        if len(self._kept) > _KEPT_TEXTS:
            self._kept = {text: self._kept[text] for text in texts}
        close: list[Similar] = most_similar(
            vectors[0], ((str(index), self._kept[text]) for index, text in enumerate(texts)), self._minimum, len(texts)
        )
        return [int(item.id) for item in close]
