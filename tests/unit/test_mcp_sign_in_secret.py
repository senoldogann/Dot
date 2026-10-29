"""Signing in to an MCP server whose authorization server registers Dot with a secret it must send and names no way of
sending it, as Supabase's does (item 816): the MCP library's own flow, through Dot's sign-in, against a server answered
in this process (``httpx2.MockTransport``, the HTTP of the client library). The token is asked for with the secret,
the way the server's metadata lists, and a client kept from before is renewed with it too."""

import asyncio
import base64
import json
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx2
import pytest
from mcp.client.auth.oauth2 import OAuthClientProvider
from mcp.shared.auth import OAuthClientInformationFull, OAuthMetadata, OAuthToken

from dotagent.config import McpServerConfig
from dotagent.mcp.signin import SignIns, StoredSignIn, kept_sign_in, read_sign_in, sign_in_path, usable_client

MCP_URL: str = "https://mcp.example.test/mcp"
AUTH: str = "https://auth.example.test"
ORIGIN: str = "http://127.0.0.1:8765"
CLIENT_ID: str = "client-1"
SECRET: str = "secret-1"
ACCESS: str = "access-1"
RENEWED: str = "access-2"


@pytest.fixture(autouse=True)
def _home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("DOT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("DOT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    yield


def _metadata(methods: list[str]) -> dict[str, object]:
    return {
        "issuer": AUTH,
        "authorization_endpoint": f"{AUTH}/authorize",
        "token_endpoint": f"{AUTH}/token",
        "registration_endpoint": f"{AUTH}/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": methods,
        "code_challenge_methods_supported": ["S256"],
    }


@dataclass
class _Supabase:
    """An MCP server and its authorization server, as Supabase's answer: the registration gives a secret and names
    no way of sending it, and the token endpoint refuses a request without the secret."""

    methods: list[str]
    sent: list[str] = field(default_factory=list)
    grants: list[str] = field(default_factory=list)

    def _secret_sent(self, request: httpx2.Request, form: dict[str, str]) -> str | None:
        header: str = request.headers.get("authorization", "")
        if header.startswith("Basic "):
            client, _, secret = base64.b64decode(header.removeprefix("Basic ")).decode().partition(":")
            return "client_secret_basic" if (client, secret) == (CLIENT_ID, SECRET) else None
        if form.get("client_secret") == SECRET and form.get("client_id") == CLIENT_ID:
            return "client_secret_post"
        return None

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        url: str = str(request.url).split("?")[0]
        if url == MCP_URL:
            if request.headers.get("authorization") in (f"Bearer {ACCESS}", f"Bearer {RENEWED}"):
                return httpx2.Response(200, json={"ok": True})
            prm: str = "https://mcp.example.test/.well-known/oauth-protected-resource/mcp"
            return httpx2.Response(401, headers={"WWW-Authenticate": f'Bearer resource_metadata="{prm}"'})
        if url == "https://mcp.example.test/.well-known/oauth-protected-resource/mcp":
            return httpx2.Response(200, json={"resource": MCP_URL, "authorization_servers": [AUTH]})
        if url == f"{AUTH}/.well-known/oauth-authorization-server":
            return httpx2.Response(200, json=_metadata(self.methods))
        if url == f"{AUTH}/register":
            asked: dict[str, object] = json.loads(request.content)
            answer = {"client_id": CLIENT_ID, "client_secret": SECRET, "client_secret_expires_at": 0}
            return httpx2.Response(201, json={**answer, "redirect_uris": asked["redirect_uris"]})
        if url == f"{AUTH}/token":
            form: dict[str, str] = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
            way: str | None = self._secret_sent(request, form)
            if way is None:
                return httpx2.Response(422, json={"message": "Required parameter: client_secret"})
            self.sent.append(way)
            self.grants.append(form["grant_type"])
            token: str = ACCESS if form["grant_type"] == "authorization_code" else RENEWED
            return httpx2.Response(
                200, json={"access_token": token, "token_type": "Bearer", "expires_in": 3600, "refresh_token": "r-1"}
            )
        return httpx2.Response(404)


def _config() -> McpServerConfig:
    return McpServerConfig(name="supabase", url=MCP_URL, oauth=True)


async def _sign_in(server: _Supabase) -> httpx2.Response:
    sign_ins: SignIns = SignIns()

    def on_link(link: str) -> None:
        state: str = parse_qs(urlsplit(link).query)["state"][0]
        asyncio.get_running_loop().call_soon(sign_ins.answer, state, "code-1", None, None)

    provider: OAuthClientProvider = sign_ins.flow(_config(), ORIGIN, on_link)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(server), auth=provider) as client:
        return await client.get(MCP_URL)


@pytest.mark.parametrize(
    ("methods", "way"),
    [
        (["client_secret_basic", "client_secret_post"], "client_secret_basic"),
        (["client_secret_post"], "client_secret_post"),
    ],
)
async def test_a_secret_the_registration_names_no_way_of_sending_is_sent_the_way_the_server_lists(
    methods: list[str], way: str
) -> None:
    server: _Supabase = _Supabase(methods)
    answer: httpx2.Response = await _sign_in(server)
    assert answer.status_code == 200
    assert server.sent == [way]
    stored: StoredSignIn | None = read_sign_in(sign_in_path("supabase"))
    assert stored is not None and stored.client is not None and stored.tokens is not None
    assert stored.client.token_endpoint_auth_method == way


def _keep(kept: StoredSignIn) -> None:
    path: Path = sign_in_path("supabase")
    path.parent.mkdir(parents=True)
    path.write_text(kept.model_dump_json(), encoding="utf-8")


async def test_a_client_kept_from_before_with_no_way_named_is_renewed_with_its_secret() -> None:
    server: _Supabase = _Supabase(["client_secret_basic", "client_secret_post"])
    metadata: OAuthMetadata = OAuthMetadata.model_validate(_metadata(server.methods))
    kept: StoredSignIn = StoredSignIn(
        redirect_uri=f"{ORIGIN}/oauth/callback",
        client=OAuthClientInformationFull(
            client_id=CLIENT_ID, client_secret=SECRET, redirect_uris=[f"{ORIGIN}/oauth/callback"]
        ),
        tokens=OAuthToken(access_token="ran-out", token_type="Bearer", expires_in=3600, refresh_token="r-0"),
        expires_at=time.time() - 60,
        metadata=metadata,
        auth_server_url=AUTH,
    )
    _keep(kept)
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(server), auth=kept_sign_in(_config())) as client:
        answer: httpx2.Response = await client.get(MCP_URL)
    assert answer.status_code == 200
    assert (server.grants, server.sent) == (["refresh_token"], ["client_secret_basic"])


def test_a_client_that_names_its_way_or_has_no_secret_is_kept_as_it_is() -> None:
    metadata: OAuthMetadata = OAuthMetadata.model_validate(_metadata(["client_secret_post"]))
    public: OAuthClientInformationFull = OAuthClientInformationFull(client_id="public", redirect_uris=[MCP_URL])
    named: OAuthClientInformationFull = OAuthClientInformationFull(
        client_id="named", client_secret=SECRET, token_endpoint_auth_method="none", redirect_uris=[MCP_URL]
    )
    assert usable_client(public, metadata) is public
    assert usable_client(named, metadata) is named
    unnamed: OAuthClientInformationFull = OAuthClientInformationFull(
        client_id="unnamed", client_secret=SECRET, redirect_uris=[MCP_URL]
    )
    assert usable_client(unnamed, None).token_endpoint_auth_method == "client_secret_basic"
