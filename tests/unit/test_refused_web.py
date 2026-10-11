"""The web that is refused (item 98): the hosts a run remembers as refusing, and the headers a plain request sends."""

from dotagent.web.client import request_headers
from dotagent.web.refused import RefusedHosts, host_of


def test_a_refusing_host_is_remembered_with_its_port_and_without_its_case_or_path() -> None:
    refused: RefusedHosts = RefusedHosts()
    assert not refused.remembers("https://Jobs.Example.com/a") and len(refused) == 0
    refused.note("https://jobs.example.com/careers?x=1")
    assert refused.remembers("https://JOBS.example.com/other/page")
    assert not refused.remembers("https://example.com/") and not refused.remembers("https://jobs.example.com:8443/")
    refused.note("http://127.0.0.1:8001/wall")
    assert refused.remembers("http://127.0.0.1:8001/open") and not refused.remembers("http://127.0.0.1:8002/open")
    assert host_of("HTTPS://Acme.Example:443/x") == "acme.example:443"


def test_a_get_sends_the_navigation_headers_of_a_browser_and_a_post_does_not() -> None:
    get: dict[str, str] = request_headers("GET", "Agent/1", {})
    assert get["User-Agent"] == "Agent/1" and get["Accept"].startswith("text/html")
    assert (get["Sec-Fetch-Dest"], get["Sec-Fetch-Mode"], get["Sec-Fetch-Site"], get["Sec-Fetch-User"]) == (
        "document",
        "navigate",
        "none",
        "?1",
    )
    assert get["Upgrade-Insecure-Requests"] == "1"
    post: dict[str, str] = request_headers("POST", "Agent/1", {"Accept": "application/json", "X-Key": "k"})
    assert "Sec-Fetch-Dest" not in post and "Upgrade-Insecure-Requests" not in post
    # An API's own headers come last and win.
    assert (post["Accept"], post["X-Key"]) == ("application/json", "k")
