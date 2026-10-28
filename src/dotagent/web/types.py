"""Value types of the web layer."""

from dotagent.core.types import FrozenModel

# A plain request turned away by a wall (item 59, item 98): 999 is LinkedIn's refusal. The page is read in the browser
# instead when the run may read quietly.
REFUSED_STATUS: frozenset[int] = frozenset({401, 403, 429, 503, 999})


class FetchedDocument(FrozenModel):
    """One answer from the web: the final address, the status, its headers, the body (cut at the size limit) and the
    redirects. The headers are kept as they came, repeats and all, for what reads a site's own words of itself
    (item 776)."""

    url: str
    status: int
    media_type: str
    charset: str | None
    headers: tuple[tuple[str, str], ...] = ()
    body: bytes
    truncated: bool
    redirected_from: tuple[str, ...]


class SearchHit(FrozenModel):
    title: str
    url: str
    snippet: str
