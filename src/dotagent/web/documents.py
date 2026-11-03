"""The documents a run's ``web_fetch`` read (item 956): the ``start`` of a continuation counts in the text a result
showed, so the next part is cut from that text, not from a new download. A page read in Dot's browser because its
plain answer is a JavaScript shell would come back as the shell (a few hundred characters, or none) when downloaded
again, and a page the browser renders again may render shorter. One per run, held by the run's tool context."""

from dataclasses import dataclass

from dotagent.web.types import FetchedDocument

# How many documents a run keeps; the oldest read goes first.
KEPT_DOCUMENTS: int = 8


@dataclass(frozen=True)
class ReadableText:
    """A document as text: its title and description (pages only) and the text the parts of a result are cut from."""

    title: str
    description: str
    text: str


@dataclass(frozen=True)
class ReadDocument:
    """A document as ``web_fetch`` showed it: the answer (its body left out), its text, how many bytes the text was made
    from when the download was cut (``None`` when it was not), the line above the text that says how it was read and
    the note below it."""

    document: FetchedDocument
    readable: ReadableText
    cut_at: int | None
    lead: str
    tail: str


def without_body(document: FetchedDocument) -> FetchedDocument:
    """``document`` without the bytes it was read from, which a kept document does not need."""
    return document.model_copy(update={"body": b""})


class ReadDocuments:
    """The documents of a run by the address asked for: ``keep`` adds one (or replaces it with a newer read), ``held``
    gives it back, ``None`` when the run did not read it or has let it go."""

    def __init__(self) -> None:
        self._documents: dict[str, ReadDocument] = {}

    def keep(self, url: str, read: ReadDocument) -> None:
        self._documents.pop(url, None)
        self._documents[url] = read
        while len(self._documents) > KEPT_DOCUMENTS:
            del self._documents[next(iter(self._documents))]

    def held(self, url: str) -> ReadDocument | None:
        return self._documents.get(url)

    def __len__(self) -> int:
        return len(self._documents)
