"""Fill from docs (item 1949): the fields of a custom provider proposed from the text of its documentation page.

:func:`propose_from_text` reads what the page states plainly: the address its examples call (the part before
``/chat/completions``, ``/responses``, ``/messages`` or ``/models``), the protocol those examples speak, and the model
ids they name. Where it finds no address or no protocol, :func:`propose_with_model` asks the fast model, once, through
the checked extraction of :mod:`dotagent.web.extract`; an address or a model id the model gives that the page does not
hold is dropped. A proposal is only that: the owner reviews it in Settings and saves it, or not."""

import re
from collections import Counter

from dotagent.config import CustomProtocol
from dotagent.core.types import FrozenModel
from dotagent.llm.router import ModelRouter
from dotagent.web.extract import Extraction, FieldSpec, extract

# An address in the text: up to a space, a quote, a bracket or a backtick.
_URL: re.Pattern[str] = re.compile(r"https?://[^\s\"'`<>()\[\]{}\\,]+")
# The ends of an address that are an endpoint, not the base: the base is what is left.
_ENDPOINTS: tuple[tuple[str, CustomProtocol | None], ...] = (
    ("/chat/completions", "chat"),
    ("/responses", "responses"),
    ("/messages", "messages"),
    ("/completions", None),
    ("/embeddings", None),
    ("/models", None),
)
# Words a page uses beside its base address.
_BASE_WORDS: re.Pattern[str] = re.compile(r"base[_ -]?url|baseurl|api[_ ]base|base address|endpoint", re.IGNORECASE)
# How far around an address the words of :data:`_BASE_WORDS` count, in characters.
_NEAR_CHARS: int = 120
# What the page says of each protocol, beside the endpoints its addresses name.
_PROTOCOL_WORDS: dict[CustomProtocol, re.Pattern[str]] = {
    "chat": re.compile(r"openai[- ]compatible|chat/completions|chat completions", re.IGNORECASE),
    "responses": re.compile(r"responses api|/v1/responses", re.IGNORECASE),
    "messages": re.compile(r"anthropic[- ]compatible|anthropic-version|/v1/messages|messages api", re.IGNORECASE),
}
# A model named in an example: a JSON or Python argument, or a command line option.
_MODEL_ARGUMENT: re.Pattern[str] = re.compile(
    r"""(?:["']model["']\s*:\s*|\bmodel\s*=\s*|--model[ =])["']?([A-Za-z0-9][A-Za-z0-9._:/@-]{1,120})["']?"""
)
# Hosts whose addresses are never a provider's API: where code and packages are kept.
_NOT_AN_API: tuple[str, ...] = ("github.com", "githubusercontent.com", "pypi.org", "npmjs.com", "w3.org")
_MODELS_PROPOSED: int = 20
# The text the fast model is given of a page.
_PAGE_CHARS: int = 60_000


class DocsProposal(FrozenModel):
    """What a documentation page says of a provider: its base address, protocol and models (``None`` and empty where
    it says nothing), ``source`` how they were found (``page``: in its text alone; ``model``: the fast model helped),
    and ``notes`` what the owner should check."""

    base_url: str | None
    protocol: CustomProtocol | None
    models: list[str]
    source: str
    notes: list[str]


def _base_of(url: str) -> tuple[str, CustomProtocol | None]:
    """The base of an address (its endpoint taken off) and the protocol that endpoint speaks."""
    trimmed: str = url.rstrip(".;:/")
    for ending, protocol in _ENDPOINTS:
        at: int = trimmed.find(ending)
        if at > len("https://"):
            return trimmed[:at], protocol
    return trimmed, None


def _is_api(url: str, page: str) -> bool:
    """Whether ``url`` may be an API's address: not the page itself, not a code or package host."""
    host: str = url.split("/")[2].lower() if url.count("/") >= 2 else ""
    page_host: str = page.split("/")[2].lower() if page.count("/") >= 2 else ""
    if any(host == known or host.endswith(f".{known}") for known in _NOT_AN_API):
        return False
    return not (host == page_host and not host.startswith("api."))


def _base_score(text: str, at: int, base: str, endpoint: bool) -> int:
    """How much an address at ``at`` looks like the base: named as one, an endpoint's, versioned, on an api host."""
    near: str = text[max(0, at - _NEAR_CHARS) : at + _NEAR_CHARS]
    host: str = base.split("/")[2] if base.count("/") >= 2 else ""
    return (
        3 * (_BASE_WORDS.search(near) is not None)
        + 2 * endpoint
        + 2 * bool(re.search(r"/v\d+(?:beta\d*)?$", base))
        + host.startswith("api.")
    )


def found_base(text: str, page: str) -> tuple[str | None, Counter[CustomProtocol]]:
    """The base address the text's examples call, the best scored, and the protocols their endpoints speak."""
    scores: Counter[str] = Counter()
    protocols: Counter[CustomProtocol] = Counter()
    for match in _URL.finditer(text):
        url: str = match.group(0)
        if not _is_api(url, page):
            continue
        base, protocol = _base_of(url)
        if protocol is not None:
            protocols[protocol] += 1
        scores[base] += _base_score(text, match.start(), base, protocol is not None)
    best: list[tuple[str, int]] = scores.most_common(1)
    return (best[0][0] if best and best[0][1] > 0 else None), protocols


def found_protocol(text: str, endpoints: Counter[CustomProtocol]) -> CustomProtocol | None:
    """The protocol the page speaks of most, its endpoints counted twice; ``None`` when two are as named."""
    counts: Counter[CustomProtocol] = Counter({protocol: 2 * count for protocol, count in endpoints.items()})
    for protocol, words in _PROTOCOL_WORDS.items():
        counts[protocol] += len(words.findall(text))
    ranked: list[tuple[CustomProtocol, int]] = counts.most_common(2)
    if not ranked or ranked[0][1] == 0 or (len(ranked) == 2 and ranked[0][1] == ranked[1][1]):
        return None
    return ranked[0][0]


def found_models(text: str) -> list[str]:
    """The model ids the page's examples name, in their order, each once."""
    named: list[str] = [match.group(1) for match in _MODEL_ARGUMENT.finditer(text)]
    return list(dict.fromkeys(named))[:_MODELS_PROPOSED]


def propose_from_text(page: str, text: str) -> DocsProposal:
    """The fields the text of the documentation page at ``page`` states plainly."""
    base, endpoints = found_base(text, page)
    protocol: CustomProtocol | None = found_protocol(text, endpoints)
    models: list[str] = found_models(text)
    notes: list[str] = []
    if base is None:
        notes.append("The page names no API address plainly.")
    if protocol is None:
        notes.append("The page does not say plainly which protocol it speaks.")
    if not models:
        notes.append("The page names no model in an example: add the models by hand or let Dot list them.")
    return DocsProposal(base_url=base, protocol=protocol, models=models, source="page", notes=notes)


# What the fast model is asked for when the page does not state the address or the protocol plainly.
DOCS_FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(
        name="base_url",
        description="The API's base address that requests are sent to, as the page gives it (often ending in /v1), "
        "without /chat/completions, /responses or /messages.",
    ),
    FieldSpec(
        name="protocol",
        description="Which API the service is compatible with: 'chat' for OpenAI Chat Completions, 'responses' for "
        "the OpenAI Responses API, 'messages' for the Anthropic Messages API.",
    ),
    FieldSpec(name="models", description="The model ids the page names for chat, separated by commas."),
)


# The protocols by the word the fast model answers with.
_PROTOCOLS: dict[str, CustomProtocol] = {"chat": "chat", "responses": "responses", "messages": "messages"}


def merged_proposal(page_proposal: DocsProposal, extraction: Extraction, text: str) -> DocsProposal:
    """``page_proposal`` with what the fast model found where it had nothing: an address or a model id only when the
    page's text holds it, a protocol only when it is one of the three."""
    fields = extraction.fields
    said_base: str = fields["base_url"].value.strip() if "base_url" in fields else ""
    said_protocol: str = fields["protocol"].value.strip().lower() if "protocol" in fields else ""
    said_models: str = fields["models"].value if "models" in fields else ""
    base: str | None = page_proposal.base_url
    if base is None and _URL.fullmatch(said_base) is not None and said_base in text:
        base = _base_of(said_base)[0]
    protocol: CustomProtocol | None = page_proposal.protocol or _PROTOCOLS.get(said_protocol)
    models: list[str] = (
        page_proposal.models
        or list(
            dict.fromkeys(
                model for model in (part.strip() for part in said_models.split(",")) if model and model in text
            )
        )[:_MODELS_PROPOSED]
    )
    notes: list[str] = [
        "Dot's fast model read the page for what it does not state plainly: check each field.",
        *(["Still no API address: type it from the page."] if base is None else []),
        *(["Still no protocol: choose the one the page describes."] if protocol is None else []),
    ]
    return DocsProposal(base_url=base, protocol=protocol, models=models, source="model", notes=notes)


def needs_model(proposal: DocsProposal) -> bool:
    """Whether the page left the address or the protocol unsaid, which the fast model is then asked for."""
    return proposal.base_url is None or proposal.protocol is None


async def propose_with_model(
    router: ModelRouter, fast_model: str, timeout_s: float, page: str, text: str
) -> DocsProposal:
    """:func:`propose_from_text`, then one call of ``fast_model`` for what the page does not state plainly."""
    plain: DocsProposal = propose_from_text(page, text)
    if not needs_model(plain):
        return plain
    extraction: Extraction = await extract(
        router, fast_model, timeout_s, "", None, page, text[:_PAGE_CHARS], DOCS_FIELDS
    )
    return merged_proposal(plain, extraction, text)
