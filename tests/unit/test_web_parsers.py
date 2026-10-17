"""The page and search parsers on fixed samples: what stays, what is left out and how the text is laid out."""

from dotagent.web.search import parse_duckduckgo
from dotagent.web.text import html_to_text

_BASE: str = "https://docs.test/guide/"
_ARTICLE: str = """<!doctype html><html lang="en"><head><title>  Guide
 to Dot </title>
<meta name="description" content="How to   use Dot.">
<script>var tracking = "never shown";</script></head><body>
<nav><a href="/">Home</a> <a href="/pricing">Pricing</a></nav>
<div role="search"><form><input name="q"> Search the docs</form></div>
<main>
<h1><a class="anchor" href="#guide">&#8203;</a>Guide</h1>
<ul class="languages">
<li><a lang="de" hreflang="de" href="/de/guide/">Deutsch</a></li>
<li><a lang="tr" hreflang="tr" href="/tr/guide/">Türkçe</a></li>
</ul>
<p>Read the <a href="setup">setup page</a> first, then <a href="#top">go on</a>.</p>
<h2>Steps</h2>
<ul><li>Install</li><li><button>Share</button></li><li>Configure <code>dot.toml</code></li></ul>
<ol><li><p>Open the app</p><p>It starts in the menu bar.</p></li><li>Pick a model</li></ol>
<table><tr><th>Name</th><th>Value</th></tr><tr><td>alpha</td><td>1</td></tr><tr><td>beta</td><td>2</td></tr></table>
<pre>first

second</pre>
</main>
<footer>Copyright text</footer>
</body></html>"""


def test_navigation_switchers_and_empty_items_are_left_out() -> None:
    page = html_to_text(_ARTICLE, _BASE)
    assert page.title == "Guide to Dot"
    assert page.description == "How to use Dot."
    for left_out in ("Home", "Pricing", "Search the docs", "Deutsch", "Türkçe", "Share", "Copyright", "tracking"):
        assert left_out not in page.text
    assert "- \n" not in page.text
    assert not [line for line in page.text.split("\n") if line.strip() in ("-", "1.", "2.")]


def test_headings_links_lists_tables_and_code_keep_their_structure() -> None:
    text = html_to_text(_ARTICLE, _BASE).text
    assert text.startswith("# Guide\n\n")
    assert "Read the [setup page](https://docs.test/guide/setup) first, then go on." in text
    assert "## Steps\n\n- Install\n- Configure `dot.toml`\n" in text
    assert "1. Open the app\n\nIt starts in the menu bar.\n\n2. Pick a model" in text
    assert "Name | Value\nalpha | 1\nbeta | 2" in text
    assert "```\nfirst\n\nsecond\n```" in text


_RESULTS: str = """<html><body><div id="links" class="results">
<div class="result results_links results_links_deep web-result">
<div class="links_main links_deep result__body"><h2 class="result__title">
<a rel="nofollow" class="result__a"
href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.python.org%2F&amp;rut=1">Welcome to <b>Python</b>.org</a></h2>
<a class="result__snippet"
href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.python.org%2F&amp;rut=1">The official home of the
<b>Python</b>  Programming Language.</a></div></div>
<div class="result results_links results_links_deep result--ad">
<h2 class="result__title"><a class="result__a"
href="https://duckduckgo.com/y.js?ad_domain=shop.test&amp;u=1">Buy Python now</a></h2>
<a class="result__snippet"
href="https://duckduckgo.com/y.js?ad_domain=shop.test&amp;u=1">Sponsored</a></div>
<div class="result results_links results_links_deep web-result">
<h2 class="result__title"><a class="result__a"
href="https://en.wikipedia.org/wiki/Python_(programming_language)">Python - Wikipedia</a></h2>
<a class="result__snippet"
href="https://en.wikipedia.org/wiki/Python_(programming_language)">General-purpose language.</a></div>
<div class="result results_links results_links_deep web-result">
<h2 class="result__title"><a class="result__a"
href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fwww.python.org%2F&amp;rut=2">Python.org again</a></h2></div>
</div></body></html>"""


def test_search_results_keep_organic_hits_once_and_unwrap_their_addresses() -> None:
    page = parse_duckduckgo(_RESULTS)
    assert [hit.url for hit in page.hits] == [
        "https://www.python.org/",
        "https://en.wikipedia.org/wiki/Python_(programming_language)",
    ]
    assert page.hits[0].title == "Welcome to Python.org"
    assert page.hits[0].snippet == "The official home of the Python Programming Language."
    assert not page.announced_no_results


def test_a_page_that_announces_no_results_has_no_hits() -> None:
    page = parse_duckduckgo('<html><body><div class="no-results">No  results.</div></body></html>')
    assert page.hits == ()
    assert page.announced_no_results
