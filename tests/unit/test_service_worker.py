"""What the phone's service worker must keep doing when the Mac is out of reach (finding 40) and when a push arrives
broken (finding 43). A service worker runs only in a browser, so this reads ``sw.js`` as text: it is a guard that the
fallback and the try/catch are still there, not a test of how they behave. ``tests/smoke/test_offline_page.py`` is the
one that goes offline in a browser."""

import re

from dotagent.server.app import STATIC_DIR

SERVICE_WORKER: str = (STATIC_DIR / "sw.js").read_text(encoding="utf-8")
# Where the worker answers a page it kept nothing of with the one it keeps for a Mac out of reach.
OFFLINE_FALLBACK: re.Pattern[str] = re.compile(
    r'event\.request\.mode === "navigate" \? await caches\.match\(OFFLINE_PAGE\)'
)
# The payload of a push read inside a try/catch, so that one that is not JSON does not stop the handler.
JSON_GUARD: re.Pattern[str] = re.compile(r"try \{\s*return event\.data\.json\(\);\s*\} catch \{")


def test_the_worker_keeps_the_offline_page_when_it_installs() -> None:
    """The page answers the first opening without a network, so it is kept when the worker installs, before any page of
    Dot is: it is in OFFLINE_FILES, which the install handler adds to its cache."""
    assert (STATIC_DIR / "offline.html").is_file(), "offline.html is not a file of the interface"
    assert 'const OFFLINE_PAGE = "/offline.html";' in SERVICE_WORKER
    assert "const OFFLINE_FILES = [OFFLINE_PAGE," in SERVICE_WORKER
    assert "cache.addAll(OFFLINE_FILES)" in SERVICE_WORKER
    install: str = SERVICE_WORKER[SERVICE_WORKER.index('addEventListener("install"') :]
    assert install.index("keepOffline()") < install.index("skipWaiting()")


def test_the_worker_answers_a_page_it_kept_nothing_of_with_the_offline_page() -> None:
    """A navigation the network fails and nothing is kept of is answered with the offline page; anything else fails as
    it did, so that a script or a font that cannot be had is still an error."""
    assert OFFLINE_FALLBACK.search(SERVICE_WORKER) is not None
    assert SERVICE_WORKER.index("caches.match(OFFLINE_PAGE)") < SERVICE_WORKER.index("throw late.error")


def test_the_page_kept_for_a_mac_out_of_reach_outlives_the_copies_of_other_builds() -> None:
    """The activate handler and keepPage delete the caches of any other build; the offline page's is of no build and is
    left alone, as the copies of the next build would take it with them."""
    assert SERVICE_WORKER.count("name !== OFFLINE_CACHE") == 2


def test_a_push_payload_that_is_not_json_shows_a_notification_rather_than_throwing() -> None:
    """A push the daemon sends is JSON, but a broken one must not end in a handler that throws: it becomes the general
    notification, and the handler goes on to read the route and the tag of a message that has them."""
    assert JSON_GUARD.search(SERVICE_WORKER) is not None
    assert '"Dot has news"' in SERVICE_WORKER
    assert "tag: message.route" in SERVICE_WORKER and "data: { route: message.route }" in SERVICE_WORKER
