"""The words Dot sends on a channel (item 572): an answer as plain text, cut to the channel's size with the link to its
chat, and the one line that tells a run waits for the owner."""

from dotagent.channels.text import as_plain_text, cut_with_link, one_line

LINK: str = "https://mac.example.ts.net/#/runs/run_1"


def test_markdown_marks_a_messaging_app_would_show_are_left_out() -> None:
    markdown: str = (
        "## Plan\n\nDo **this** first, then `that`.\n\n```python\nprint('hi')\n```\n\n- one\n- two\n\n"
        "See [the docs](https://example.com/docs) and ![a chart](https://example.com/c.png).\n\n\n\nEnd."
    )
    assert as_plain_text(markdown) == (
        "Plan\n\nDo this first, then that.\n\nprint('hi')\n\n- one\n- two\n\n"
        "See the docs (https://example.com/docs) and a chart (https://example.com/c.png).\n\nEnd."
    )


def test_a_lone_star_or_underscore_stays_where_it_means_something() -> None:
    assert as_plain_text("2 * 3 = 6 and snake_case_name") == "2 * 3 = 6 and snake_case_name"


def test_an_answer_that_fits_is_sent_whole_and_a_longer_one_is_cut_with_the_link() -> None:
    assert cut_with_link("short answer", 100, LINK) == "short answer"
    long: str = "word " * 1000
    cut: str = cut_with_link(long, 300, LINK)
    assert len(cut) <= 300
    assert cut.endswith(LINK)
    assert "The rest of the answer is in Dot" in cut
    assert cut.startswith("word word")


def test_a_cut_comes_at_a_line_break_when_one_is_close() -> None:
    lines: str = "\n".join(f"line number {number} of the answer" for number in range(100))
    cut: str = cut_with_link(lines, 400, LINK)
    head: str = cut.split("\n\n… The rest")[0]
    assert all(line.startswith("line number") and line.endswith("of the answer") for line in head.splitlines())


def test_one_line_joins_the_words_and_marks_a_cut() -> None:
    assert one_line("a\n\n  b   **c**", 40) == "a b c"
    cut: str = one_line("x" * 50, 20)
    assert len(cut) == 20 and cut.endswith("…")
