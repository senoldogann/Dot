"""The pure parts of the crawler and of typed extraction: scopes, links, the frontier, the shell rule, robots rules,
the extraction tool and the check of its answer."""

import pytest

from dotagent.core.errors import ProviderProtocolError
from dotagent.web.crawl import (
    Scope,
    in_scope,
    looks_like_shell,
    normalized,
    page_links,
    plan_frontier,
    robots_rules,
    scope_of,
)
from dotagent.web.extract import FieldSpec, checked_extraction, extraction_tool

SITE: str = "https://docs.example.com"
HTML: str = """<!doctype html><html><head><base href="/guide/"></head><body>
<a href="intro.html">Intro</a> <a href="/guide/setup.html#top">Setup</a> <a href="intro.html">Again</a>
<a href="https://other.example.org/x">Other</a> <a href="mailto:a@b.c">Mail</a> <a href="javascript:void(0)">JS</a>
<a href="/blog/post">Blog</a></body></html>"""


def test_links_are_absolute_without_fragments_and_each_once() -> None:
    assert page_links(HTML, f"{SITE}/guide/") == (
        f"{SITE}/guide/intro.html",
        f"{SITE}/guide/setup.html",
        "https://other.example.org/x",
        f"{SITE}/blog/post",
    )
    assert normalized("HTTPS://Docs.Example.com") == "https://docs.example.com/"


def test_a_scope_is_the_folder_or_the_host_and_the_frontier_stays_in_it() -> None:
    prefix: Scope = scope_of("prefix", f"{SITE}/guide/intro.html")
    assert prefix.url == f"{SITE}/guide/"
    assert scope_of("prefix", f"{SITE}/guide/").url == f"{SITE}/guide/"
    host: Scope = scope_of("host", f"{SITE}/guide/intro.html")
    assert host.url == f"{SITE}/"
    links = page_links(HTML, f"{SITE}/guide/")
    assert plan_frontier(links, prefix, ()) == (f"{SITE}/guide/intro.html", f"{SITE}/guide/setup.html")
    assert plan_frontier(links, host, (f"{SITE}/guide/intro.html",)) == (
        f"{SITE}/guide/setup.html",
        f"{SITE}/blog/post",
    )
    assert not in_scope("http://docs.example.com/guide/x", prefix)


def test_a_thin_page_with_the_scripts_of_an_app_is_a_shell() -> None:
    shell: bytes = b"<html><script></script><script></script><script></script><div id=app></div></html>"
    assert looks_like_shell(shell, 10)
    assert not looks_like_shell(shell, 5000)
    assert not looks_like_shell(b"<html><script></script><p>short</p></html>", 10)


def test_robots_rules_are_read_and_an_empty_file_allows_everything() -> None:
    rules = robots_rules("User-agent: *\nDisallow: /private/\n")
    assert rules.can_fetch("Dot", f"{SITE}/guide/")
    assert not rules.can_fetch("Dot", f"{SITE}/private/x")
    assert robots_rules("").can_fetch("Dot", f"{SITE}/private/x")


FIELDS: tuple[FieldSpec, ...] = (
    FieldSpec(name="price", description="The price with its currency."),
    FieldSpec(name="weight", description="The weight in grams."),
)
TEXT: str = "The Lamp costs 24,90 EUR and weighs about 800 g."


def test_the_extraction_tool_requires_every_field_as_a_value_and_a_quote() -> None:
    tool = extraction_tool(FIELDS)
    assert tool.parameters["required"] == ["price", "weight"]
    price = tool.parameters["properties"]["price"]  # type: ignore[index]
    assert price["required"] == ["value", "quote"]  # type: ignore[index]


def test_a_quote_that_is_in_the_source_verifies_the_value_and_one_that_is_not_does_not() -> None:
    extraction = checked_extraction(
        "page",
        TEXT,
        FIELDS,
        {
            "price": {"value": "24,90 EUR", "quote": "costs  24,90 eur"},
            "weight": {"value": "900 g", "quote": "weighs 900 g"},
        },
    )
    assert extraction.fields["price"].verified
    assert extraction.fields["price"].value == "24,90 EUR"
    assert not extraction.fields["weight"].verified
    empty = checked_extraction("page", TEXT, FIELDS[:1], {"price": {"value": "", "quote": ""}})
    assert (empty.fields["price"].value, empty.fields["price"].verified) == ("", False)
    with pytest.raises(ProviderProtocolError):
        checked_extraction("page", TEXT, FIELDS, {"price": {"value": "x"}, "weight": "none"})
