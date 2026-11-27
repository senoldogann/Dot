"""The web UI's Content-Security-Policy lets a video the page itself made play, and nothing else (item 1992, the owner
approved the line on 2026-11-27): ``media-src blob:`` and no other source, so no page, site or data address plays."""

from dotagent.server.guard import SECURITY_HEADERS


def test_the_ui_policy_takes_media_from_a_blob_the_page_made_and_from_nothing_else() -> None:
    policy: str = dict(SECURITY_HEADERS)["Content-Security-Policy"]
    directives: dict[str, str] = {
        name: value.strip()
        for name, _, value in (part.strip().partition(" ") for part in policy.split(";") if part.strip())
    }
    assert directives["media-src"] == "blob:"
    # The rest of the policy keeps what it was: nothing is fetched by default and no script comes from elsewhere.
    assert directives["default-src"] == "'none'"
    assert directives["script-src"] == "'self'"
    assert "*" not in policy
