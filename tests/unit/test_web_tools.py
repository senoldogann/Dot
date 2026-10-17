"""The plain judgments of the web and browser tools (item 56): what a guessed address says, which front-page links it
may have meant, how a missing page is reported, and how an oversized request is cut rather than refused."""

from dotagent.browser.types import Step
from dotagent.tools.batch import batch_cut_note, first_steps
from dotagent.tools.web import front_page, likely_links, path_words, render_http_failure, wanted_results
from dotagent.web.types import FetchedDocument

_LINKS: tuple[str, ...] = (
    "https://acme.example/",
    "https://acme.example/about/",
    "https://acme.example/company/open-roles/",
    "https://acme.example/blog/careers-day-2026/",
    "https://jobs.acme.example/careers/",
    "https://acme.example/careers/",
    "https://acme.example/contact/",
)


def _document(status: int) -> FetchedDocument:
    return FetchedDocument(
        url="https://acme.example/careers/",
        status=status,
        media_type="text/html",
        charset="utf-8",
        body=b"",
        truncated=False,
        redirected_from=(),
    )


def test_the_words_of_an_address_are_its_path_and_query_and_the_front_page_is_the_host() -> None:
    assert path_words("https://acme.example/en/Open-Roles/?team=ml&x=1") == frozenset({"open", "roles", "team"})
    assert path_words("https://acme.example/") == frozenset()
    assert front_page("https://acme.example/careers/?x=1#top") == "https://acme.example/"


def test_the_likely_links_share_a_word_stay_on_the_host_and_leave_the_address_itself_out() -> None:
    assert likely_links(_LINKS, "https://acme.example/careers/", 12) == ("https://acme.example/blog/careers-day-2026/",)
    assert likely_links(_LINKS, "https://acme.example/open-roles/", 12) == ("https://acme.example/company/open-roles/",)
    assert likely_links(_LINKS, "https://acme.example/careers/", 1) == ("https://acme.example/blog/careers-day-2026/",)
    assert likely_links(_LINKS * 2, "https://acme.example/careers/", 1) == (
        "https://acme.example/blog/careers-day-2026/",
    )
    assert likely_links(_LINKS, "https://acme.example/nothing-like-it/", 12) == ()


def test_a_missing_page_says_so_and_lists_the_links_or_why_there_are_none() -> None:
    listed: str = render_http_failure(
        "https://acme.example/careers/", _document(404), "", "n", ("https://acme.example/company/open-roles/",), "", ""
    )
    assert listed.startswith("web_fetch got HTTP 404 from https://acme.example/careers/.\nThe page is not there.")
    assert "- https://acme.example/company/open-roles/" in listed
    none: str = render_http_failure("https://acme.example/careers/", _document(410), "", "n", (), "", "")
    assert "links to nothing that shares a word" in none
    unread: str = render_http_failure(
        "https://acme.example/careers/", _document(404), "", "n", (), "it answered HTTP 500", ""
    )
    assert "could not be read either: it answered HTTP 500" in unread
    # Another error status gets no such lines; a refusal points at the browser.
    refused: str = render_http_failure("https://acme.example/careers/", _document(403), "", "n", (), "", "")
    assert "not there" not in refused and "browser_open" in refused and "web_search with the address" in refused
    # When the run's browser was asked and could not read the page, the result says so before the next rung.
    unopened: str = render_http_failure(
        "https://acme.example/careers/", _document(403), "", "n", (), "", "Dot's browser could not read it either: x."
    )
    assert unopened.split("\n")[1] == "Dot's browser could not read it either: x."


def test_an_oversized_request_is_cut_to_its_limit_and_the_result_says_so() -> None:
    assert wanted_results(8) == (8, "")
    assert wanted_results(20) == (10, " (max_results 20 lowered to 10, the most a search returns)")
    steps: list[Step] = [Step(action="press", key=str(number)) for number in range(7)]
    assert first_steps(steps, 5) == steps[:5]
    assert batch_cut_note(7, 5) == (
        "The batch had 7 steps; the first 5 ran (the most a batch holds). Look at the result, then send the remaining "
        "2."
    )
    assert batch_cut_note(5, 5) == ""
