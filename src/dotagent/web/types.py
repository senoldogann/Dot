"""Value types of the web layer."""

from dotagent.core.types import FrozenModel


class FetchedDocument(FrozenModel):
    """One answer from the web: the final address, the status, the body (cut at the size limit) and the redirects."""

    url: str
    status: int
    media_type: str
    charset: str | None
    body: bytes
    truncated: bool
    redirected_from: tuple[str, ...]


class SearchHit(FrozenModel):
    title: str
    url: str
    snippet: str
