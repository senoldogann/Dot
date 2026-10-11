"""The Anthropic rung against the live API (item 69): one search through the Messages API's web_search tool, with the
key of this machine; skipped without it."""

import pytest

from dotagent.config import load_default_config
from dotagent.secrets import resolve_secret
from dotagent.web.client import WebClient
from dotagent.web.search import AnthropicSearch, search_anthropic
from dotagent.web.types import SearchHit

pytestmark = [pytest.mark.internet, pytest.mark.asyncio]

_KEY_ENV: str = load_default_config().providers.anthropic.api_key_env
requires_key = pytest.mark.skipif(resolve_secret(_KEY_ENV) is None, reason=f"needs {_KEY_ENV}")


@requires_key
async def test_one_search_comes_back_with_addresses_and_titles() -> None:
    config = load_default_config()
    key: str | None = resolve_secret(_KEY_ENV)
    assert key is not None
    client: WebClient = WebClient(config.web, (), ())
    try:
        relay: AnthropicSearch = AnthropicSearch(api_key=key, model=config.models.search.split(":", 1)[1])
        hits: tuple[SearchHit, ...] = await search_anthropic(client, relay, "Anthropic Claude web search tool docs", 5)
    finally:
        await client.aclose()
    assert 1 <= len(hits) <= 5, hits
    assert all(hit.url.startswith("http") and hit.title for hit in hits), hits
