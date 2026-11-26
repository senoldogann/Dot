"""What the guard lets reach the hooks (``docs/hooks.md``): a call, ``POST /api/hooks/<id>`` in that exact shape, needs
no token (its signature is checked behind the guard), while every other path under ``/api/hooks`` makes or changes a way
into Dot and answers the installation's token alone, never a phone's key. A call keeps every other check of the guard:
the Host must be this Mac's or a reach name, or one of ``[server] hook_hosts``, which reaches the call and nothing else,
and a page of another site is refused."""

import httpx
import pytest
from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.types import Receive, Scope, Send

from dotagent.server.bodylimit import MOST, PUBLIC_MOST, body_most
from dotagent.server.guard import LocalGuard, check_request, is_hook_call, needs_main_token, needs_token

PORT: int = 8765
TOKEN: str = "MARKER-0123456789abcdef"
LOCAL_HOST: str = f"127.0.0.1:{PORT}"
CALL: str = "/api/hooks/0123456789ab"
REACH: frozenset[str] = frozenset({"mac.tailnet.ts.net"})
FUNNEL: str = "mac.tailnet.ts.net:8443"


async def _ok(scope: Scope, receive: Receive, send: Send) -> None:
    await PlainTextResponse(f"served {Request(scope).url.path}")(scope, receive, send)


def _client(hook_hosts: frozenset[str]) -> httpx.AsyncClient:
    guard: LocalGuard = LocalGuard(_ok, token=TOKEN, port=PORT, reach=REACH, hook_hosts=hook_hosts)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=guard), base_url=f"http://{LOCAL_HOST}")


def test_only_the_exact_shape_of_a_call_goes_without_the_token() -> None:
    assert is_hook_call(CALL) and not needs_token(CALL)
    for path in (
        "/api/hooks",
        "/api/hooks/",
        "/api/hooks/0123456789ab/rotate",
        "/api/hooks/0123456789ab/delete",
        "/api/hooks/0123456789AB",
        "/api/hooks/0123456789abc",
        "/api/hooks/0123456789ab/",
    ):
        assert not is_hook_call(path) and needs_token(path), path
        assert needs_main_token(path), path
    assert check_request(CALL, LOCAL_HOST, None, None, None, None, TOKEN, PORT, frozenset()) is None
    refused = check_request("/api/hooks", LOCAL_HOST, None, None, None, None, TOKEN, PORT, frozenset())
    assert refused is not None and refused.status == 401
    # A phone's key is let in elsewhere, never to the hooks' own routes.
    by_phone = check_request(
        "/api/hooks/0123456789ab/rotate", LOCAL_HOST, None, None, None, "Bearer phone.x", TOKEN, PORT, frozenset(), "p1"
    )
    assert by_phone is not None and by_phone.status == 403


def test_a_call_takes_the_hooks_own_body_limit_and_the_rest_theirs() -> None:
    assert body_most(CALL, needs_token(CALL), is_hook_call, MOST, PUBLIC_MOST, 65_536) == 65_536
    assert body_most("/api/hooks", needs_token("/api/hooks"), is_hook_call, MOST, PUBLIC_MOST, 65_536) == MOST
    assert body_most("/api/health", needs_token("/api/health"), is_hook_call, MOST, PUBLIC_MOST, 65_536) == PUBLIC_MOST


@pytest.mark.asyncio
async def test_a_call_is_reached_on_this_mac_and_the_reach_names_and_a_hook_host_reaches_it_alone() -> None:
    async with _client(frozenset({FUNNEL})) as client:
        assert (await client.post(CALL)).status_code == 200
        assert (await client.post(CALL, headers={"Host": "mac.tailnet.ts.net"})).status_code == 200
        # The tunnel's name reaches the call and nothing else, whatever token it carries.
        assert (await client.post(CALL, headers={"Host": FUNNEL})).status_code == 200
        for path in ("/api/status", "/api/hooks", "/", "/api/hooks/0123456789ab/rotate"):
            answer: httpx.Response = await client.get(
                path, headers={"Host": FUNNEL, "Authorization": f"Bearer {TOKEN}"}
            )
            assert answer.status_code == 421, path
        assert (await client.post(CALL, headers={"Host": "evil.example"})).status_code == 421
        # A page of another site cannot make a browser call a hook.
        assert (await client.post(CALL, headers={"Origin": "https://evil.example"})).status_code == 403
    async with _client(frozenset()) as closed:
        assert (await closed.post(CALL, headers={"Host": FUNNEL})).status_code == 421
