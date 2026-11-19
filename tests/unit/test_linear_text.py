"""Text a page, a mail or a site wrote is read in time that grows with its length (item 1242). Each pattern below went
back over a long run of the same characters from each of its places, so that 40,000 tabs took 3 s, 16,000 letters 12 s,
and a megabyte half an hour, with the whole daemon held: Python's ``re`` keeps the interpreter while it matches. The
inputs are long enough that the old patterns took more than a minute; the bound is far above what they take now."""

import time
from collections.abc import Callable

from dotagent.agent.continuity import SUMMARY_SECTIONS, missing_sections
from dotagent.browser.network import hidden_text
from dotagent.browser.snapshot import squeeze_text
from dotagent.channels.text import as_plain_text
from dotagent.core.text import without_trailing_blanks
from dotagent.desktop.codes import extract_code
from dotagent.tools.recon import page_title, technology
from dotagent.video.watch import parse_vtt
from dotagent.web.text import html_to_text

_MOST_S: float = 2.0
_LONG: int = 200_000


def _quick[T](read: Callable[[str], T], text: str) -> T:
    started: float = time.monotonic()
    found: T = read(text)
    assert time.monotonic() - started < _MOST_S
    return found


def test_a_long_run_of_tabs_and_spaces_that_no_line_break_ends_is_read_in_a_moment() -> None:
    read: str = _quick(lambda page: html_to_text(page, "https://docs.test/").text, "<pre>" + "\t" * _LONG + "x</pre>")
    assert read.startswith("```") and read.endswith("x\n```")
    assert _quick(squeeze_text, " \t" * (_LONG // 2) + "x") == "x"
    assert without_trailing_blanks("a \t\n b\t\n\nc  ") == "a\n b\n\nc"
    assert squeeze_text("a  \r\n\n\n\nb \n") == "a\n\nb"


def test_a_long_run_of_letters_is_read_once_and_a_secrets_value_is_still_hidden() -> None:
    _quick(hidden_text, "a" * _LONG)
    _quick(hidden_text, "token" * (_LONG // 5))
    _quick(hidden_text, "0123456789abcdef" * (_LONG // 16))
    assert hidden_text('access_token=abc&x=1 "api-key": "k1" csrf_token: v2 my.session-id=zz') == (
        'access_token=[hidden]&x=1 "api-key": [hidden] csrf_token: [hidden] my.session-id=[hidden]'
    )
    assert hidden_text("tokens are fine, monkey=1") == "tokens are fine, monkey=1"


def test_a_mail_of_many_empty_lines_gives_its_code_in_a_moment() -> None:
    assert _quick(extract_code, "\n" * _LONG + "Hi\n\n  482 913 is yours") == "482913"
    assert extract_code("Your code\n\n 482913\nthanks") == "482913"


def test_an_unclosed_tag_of_a_site_is_read_in_a_moment() -> None:
    _quick(parse_vtt, "WEBVTT\n\n00:00:00.000 --> 00:00:01.000\n" + "<" * _LONG + "\n")
    assert _quick(page_title, "<title" * (_LONG // 6)) == ""
    _quick(lambda body: technology({}, body), "<meta " * (_LONG // 6))
    assert page_title("<html><title>Shop</title>") == "Shop"
    assert "WordPress 6.5" in technology({}, '<meta name="generator" content="WordPress 6.5">')


def test_a_summary_of_many_blank_lines_is_checked_for_its_headings_in_a_moment() -> None:
    assert _quick(missing_sections, "\n" * _LONG) == SUMMARY_SECTIONS
    assert _quick(missing_sections, "-" * _LONG) == SUMMARY_SECTIONS
    whole: str = "\n".join(f"## {section}\nx" for section in SUMMARY_SECTIONS)
    assert missing_sections(whole) == ()
    assert missing_sections(whole.replace("## ", "1. **").replace("\nx", "** - x")) == ()


def test_a_message_of_many_open_brackets_is_written_plainly_in_a_moment() -> None:
    _quick(as_plain_text, "[" * _LONG)
    _quick(as_plain_text, "[[a]" * (_LONG // 4))
    assert as_plain_text("see [the page](https://a.test/x) now") == "see the page (https://a.test/x) now"
    assert as_plain_text("see [page [1]](https://a.test/x) now") == "see page [1] (https://a.test/x) now"
    assert as_plain_text("[a [1] and [2]](https://a.test/x)") == "a [1] and [2] (https://a.test/x)"
