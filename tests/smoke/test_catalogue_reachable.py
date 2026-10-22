"""Every service of the catalogue is still there as Dot reaches it (item 535), checked by read-only requests alone: one
that signs in answers an initialize without a token with 401, and its sign-in metadata, read in the order the MCP
library reads it, names an authorization server that registers clients, takes a client without a secret and does
PKCE with S256; one open to all answers an initialize; one that takes a key at an address is there; one started on
this Mac names a package its registry has, not deprecated, with the program it starts. No client is registered here:
item 535 took each new service through Dot's own sign-in once, by hand."""

import re
from collections.abc import Iterator
from urllib.parse import urlsplit

import httpx
import pytest

from dotagent.mcp.catalogue import MCP_SERVICES, McpService

pytestmark = pytest.mark.internet

_VERSION: str = "2025-06-18"
_INITIALIZE: dict[str, object] = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": _VERSION, "capabilities": {}, "clientInfo": {"name": "dot-check", "version": "0"}},
}
_POST_HEADERS: dict[str, str] = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": _VERSION,
}
_RESOURCE_METADATA: re.Pattern[str] = re.compile(r'resource_metadata="([^"]+)"')
# Those whose metadata names no client without a secret, yet registers one: Dot's own client registered with GitLab,
# Miro and Heroku in item 413, and Supabase's docs say so (the catalogue's docstring).
_NONE_UNLISTED: frozenset[str] = frozenset({"supabase", "gitlab", "miro", "heroku"})
# A version or a range written after a package's name (``@latest``, ``@1.2``, ``==1.2``), and a scope before it.
_PACKAGE_VERSION: re.Pattern[str] = re.compile(r"^(@?[^@=<>]+)(?:@.*|==.*|>=.*)?$")


def _origin_and_path(url: str) -> tuple[str, str]:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}", parts.path.rstrip("/")


def _json(client: httpx.Client, url: str) -> dict[str, object] | None:
    response: httpx.Response = client.get(url, headers={"Accept": "application/json", "MCP-Protocol-Version": _VERSION})
    if response.status_code != 200:
        return None
    try:
        data: object = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _authorization_server(client: httpx.Client, url: str, challenge: str) -> str:
    """The authorization server the protected resource metadata names: at the address the 401 gives, else at the
    well-known address with the path and then without it; the server's own origin when it has none."""
    origin, path = _origin_and_path(url)
    hinted: re.Match[str] | None = _RESOURCE_METADATA.search(challenge)
    candidates: list[str] = [hinted.group(1)] if hinted else []
    if path:
        candidates.append(f"{origin}/.well-known/oauth-protected-resource{path}")
    candidates.append(f"{origin}/.well-known/oauth-protected-resource")
    for candidate in candidates:
        metadata: dict[str, object] | None = _json(client, candidate)
        servers: object = None if metadata is None else metadata.get("authorization_servers")
        if isinstance(servers, list) and servers and isinstance(servers[0], str):
            return servers[0]
    return origin


def _authorization_metadata(client: httpx.Client, issuer: str) -> dict[str, object]:
    origin, path = _origin_and_path(issuer)
    candidates: list[str] = (
        [
            f"{origin}/.well-known/oauth-authorization-server{path}",
            f"{origin}/.well-known/openid-configuration{path}",
            f"{origin}{path}/.well-known/openid-configuration",
        ]
        if path
        else [f"{origin}/.well-known/oauth-authorization-server", f"{origin}/.well-known/openid-configuration"]
    )
    for candidate in candidates:
        metadata: dict[str, object] | None = _json(client, candidate)
        if metadata is not None and metadata.get("authorization_endpoint"):
            return metadata
    raise AssertionError(f"no authorization server metadata for {issuer}: {candidates}")


def _package(service: McpService) -> tuple[str, str]:
    """The registry and the name of the package a service started on this Mac runs: npx's first word that is not an
    option, uvx's ``--from`` or its first word, without the version written after it."""
    args: list[str] = list(service.args)
    if service.command == "uvx" and "--from" in args:
        word: str = args[args.index("--from") + 1]
    else:
        word = next(arg for arg in args if not arg.startswith("-"))
    matched: re.Match[str] | None = _PACKAGE_VERSION.match(word)
    assert matched is not None, (service.id, word)
    return ("npm" if service.command == "npx" else "pypi"), matched.group(1)


@pytest.fixture(scope="module")
def client() -> Iterator[httpx.Client]:
    with httpx.Client(timeout=30, follow_redirects=False) as opened:
        yield opened


@pytest.mark.parametrize("service", [one for one in MCP_SERVICES if one.url is not None], ids=lambda one: one.id)
def test_a_service_at_an_address_answers_as_its_entry_says(client: httpx.Client, service: McpService) -> None:
    assert service.url is not None
    answer: httpx.Response = client.post(service.url, json=_INITIALIZE, headers=_POST_HEADERS)
    if service.signs_in:
        # Dot's client signs in on a 401, and only then; another answer leaves it with no way in.
        assert answer.status_code == 401, (service.id, answer.status_code, answer.text[:200])
        issuer: str = _authorization_server(client, service.url, answer.headers.get("www-authenticate", ""))
        metadata: dict[str, object] = _authorization_metadata(client, issuer)
        methods: object = metadata.get("token_endpoint_auth_methods_supported")
        challenges: object = metadata.get("code_challenge_methods_supported")
        assert metadata.get("registration_endpoint"), (service.id, "no registration")
        assert service.id in _NONE_UNLISTED or (isinstance(methods, list) and "none" in methods), (service.id, methods)
        assert isinstance(challenges, list) and "S256" in challenges, (service.id, challenges)
    elif service.secrets:
        # Without its key it may refuse, or answer what it gives to all (Apollo's docs).
        assert answer.status_code in (200, 401, 403), (service.id, answer.status_code, answer.text[:200])
    else:
        assert answer.status_code == 200, (service.id, answer.status_code, answer.text[:200])
        assert "serverInfo" in answer.text, (service.id, answer.text[:200])


@pytest.mark.parametrize(
    "service", [one for one in MCP_SERVICES if one.command in ("npx", "uvx")], ids=lambda one: one.id
)
def test_a_service_started_on_this_mac_names_a_package_its_registry_has(
    client: httpx.Client, service: McpService
) -> None:
    registry, name = _package(service)
    if registry == "npm":
        answer: httpx.Response = client.get(f"https://registry.npmjs.org/{name.replace('/', '%2F')}/latest")
        assert answer.status_code == 200, (service.id, name, answer.status_code)
        latest: dict[str, object] = answer.json()
        assert not latest.get("deprecated"), (service.id, name, latest.get("deprecated"))
        assert latest.get("bin"), (service.id, name, "no program to start")
    else:
        answer = client.get(f"https://pypi.org/pypi/{name}/json")
        assert answer.status_code == 200, (service.id, name, answer.status_code)
        assert answer.json()["info"]["version"], (service.id, name)
