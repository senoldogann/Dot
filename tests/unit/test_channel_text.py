"""The words Dot sends on a channel (item 572): an answer as plain text, cut to the channel's size with the link to its
chat, and the one line that tells a run waits for the owner. A messaging app draws no Markdown, so what an answer holds
of it reads clean (item 617): bullets, headings on lines of their own, tables a row to a line, none of the marks. On a
phone's narrow screen the items of a list with a long one stand apart and a heading ends with a colon (item 627)."""

import pytest

from dotagent.channels.text import as_plain_text, cut_with_link, line_kinds, one_line

LINK: str = "https://mac.example.ts.net/#/runs/run_1"


def test_markdown_marks_a_messaging_app_would_show_are_left_out() -> None:
    markdown: str = (
        "## Plan\n\nDo **this** first, then `that`.\n\n```python\nprint('hi')\n```\n\n- one\n- two\n\n"
        "See [the docs](https://example.com/docs) and ![a chart](https://example.com/c.png).\n\n\n\nEnd."
    )
    assert as_plain_text(markdown) == (
        "Plan:\n\nDo this first, then that.\n\nprint('hi')\n\n• one\n• two\n\n"
        "See the docs (https://example.com/docs) and a chart (https://example.com/c.png).\n\nEnd."
    )


def test_a_lone_star_or_underscore_stays_where_it_means_something() -> None:
    assert as_plain_text("2 * 3 = 6 and snake_case_name") == "2 * 3 = 6 and snake_case_name"
    assert as_plain_text("Match src/*.py and tests/*.py, 2*3*4 and a_b_c.") == (
        "Match src/*.py and tests/*.py, 2*3*4 and a_b_c."
    )


@pytest.mark.parametrize("mark", ["-", "*", "+"])
def test_list_items_read_with_a_bullet_and_a_nested_one_with_a_smaller_bullet(mark: str) -> None:
    markdown: str = f"Steps:\n{mark} one\n{mark} two\n  {mark} nested\n    {mark} deeper\n{mark} three"
    assert as_plain_text(markdown) == "Steps:\n• one\n• two\n  ◦ nested\n    ◦ deeper\n• three"


def test_a_nested_list_is_told_by_how_much_deeper_it_stands_not_by_a_width() -> None:
    four: str = "- one\n    - four spaces\n        - eight\n- two\n\t- a tab"
    assert as_plain_text(four) == "• one\n  ◦ four spaces\n    ◦ eight\n• two\n  ◦ a tab"


def test_numbered_items_stay_as_they_are_written() -> None:
    assert as_plain_text("1. one\n2. two\n10) ten") == "1. one\n2. two\n10) ten"
    assert as_plain_text("1. one\n   - detail\n2. two") == "1. one\n  ◦ detail\n2. two"


def test_tasks_wear_a_box_and_an_empty_item_is_left_out() -> None:
    assert as_plain_text("- [ ] to do\n- [x] done\n- [X] done too\n- \n-\n- real") == (
        "☐ to do\n☑ done\n☑ done too\n• real"
    )


def test_a_heading_stands_on_a_line_of_its_own_with_a_blank_line_before_it_and_a_colon_after_it() -> None:
    markdown: str = "Intro line\n## First\nbody one\n### Second ###\nbody two\n#### \n# Top"
    # The last heading has no words after it: it is no title of anything, and takes no colon (item 627).
    assert as_plain_text(markdown) == "Intro line\n\nFirst:\nbody one\n\nSecond:\nbody two\n\nTop"
    # The first line of the text needs no blank line before it, and a hash that is no heading stays.
    assert as_plain_text("# Title\ntext") == "Title:\ntext"
    assert as_plain_text("#hashtag and C# stay") == "#hashtag and C# stay"


def test_a_heading_that_ends_in_a_mark_of_its_own_keeps_it() -> None:
    assert as_plain_text("## What changed?\nA lot.") == "What changed?\nA lot."
    assert as_plain_text("## Steps:\nOne.\n## Done!\nYes.\n## Plan (draft)\nNo.") == (
        "Steps:\nOne.\n\nDone!\nYes.\n\nPlan (draft):\nNo."
    )
    assert as_plain_text("## Sonuç\nTamam.\n## Özet 2\nBitti.") == "Sonuç:\nTamam.\n\nÖzet 2:\nBitti."


def test_a_horizontal_rule_goes_and_a_quotes_marks_go_with_it() -> None:
    assert as_plain_text("above\n\n---\n\nbelow\n***\n___\n* * *\n- - -\nend") == "above\n\nbelow\n\nend"
    assert as_plain_text("> quoted line\n> > nested\n>no space\n\nplain") == "quoted line\nnested\nno space\n\nplain"
    assert as_plain_text("> - a list in a quote\n> - two") == "• a list in a quote\n• two"


def test_a_table_reads_one_row_a_line_its_cells_joined_and_its_rule_line_gone() -> None:
    table: str = "| Name | Size |\n|:--|--:|\n| shot.png | 3 MB |\n| notes | |\n\nAfter the table."
    assert as_plain_text(table) == "Name · Size\nshot.png · 3 MB\nnotes · –\n\nAfter the table."
    # Without outer pipes, with marks in the cells and an escaped pipe, which is a cell's own.
    assert as_plain_text("a | b\n---|---\n**x** | `y\\|z`") == "a · b\nx · y|z"


def test_lines_that_only_look_like_a_table_stay() -> None:
    assert as_plain_text("one | two\nnot a rule\n") == "one | two\nnot a rule"
    # A rule line with another number of cells than the line above it is none, and a lone --- is a horizontal rule.
    assert as_plain_text("a | b\n---|---|---\nafter") == "a | b\n---|---|---\nafter"
    assert as_plain_text("a | b\n---\nafter") == "a | b\n\nafter"


def test_the_lines_of_a_block_of_code_stay_as_they_are_written() -> None:
    markdown: str = (
        "```yaml\n- item\n---\n# comment\n> redirect\n| a | b |\n|---|---|\n**bold**\n```\n"
        "after **bold** and *it* and _it2_"
    )
    assert as_plain_text(markdown) == (
        "- item\n---\n# comment\n> redirect\n| a | b |\n|---|---|\n**bold**\nafter bold and it and it2"
    )
    # A fence of tildes, a fence with more marks than the line in it, and one never closed.
    assert as_plain_text("~~~\n- a\n~~~\n- b") == "- a\n• b"
    assert as_plain_text("````\n```\n- a\n```\n````") == "```\n- a\n```"
    assert as_plain_text("text\n```\n- unclosed\n# still code") == "text\n- unclosed\n# still code"


def test_a_fence_indented_under_a_list_item_is_a_fence_and_three_ticks_on_one_line_are_a_code_span() -> None:
    nested: str = "1. Install:\n    ```bash\n    - not a bullet\n    brew install x\n    ```\n2. Done"
    assert as_plain_text(nested) == "1. Install:\n    - not a bullet\n    brew install x\n2. Done"
    tabbed: str = "- Run:\n\t~~~\n\t# not a heading\n\t~~~\n- Next"
    assert as_plain_text(tabbed) == "• Run:\n\t# not a heading\n• Next"
    assert as_plain_text("Use ```ls -la``` here, and then - not a list") == "Use ls -la here, and then - not a list"
    assert line_kinds(["```ls```", "- x"]) == ["prose", "prose"]


def test_marks_around_words_go_and_a_code_span_keeps_what_is_in_it() -> None:
    assert as_plain_text("**bold** __bold__ ~~gone~~ *it* _it_ ***both***") == "bold bold gone it it both"
    assert (
        as_plain_text("Use `**not bold**` and `a_b_c` and **use `x` here**.")
        == "Use **not bold** and a_b_c and use x here."
    )
    assert as_plain_text("Edit `__init__.py` and `*.py`.") == "Edit __init__.py and *.py."


def test_a_link_keeps_its_address_after_its_words_and_an_address_alone_stays() -> None:
    assert as_plain_text("See [docs](https://x.example/a_(b)) and [https://y.example](https://y.example).") == (
        "See docs (https://x.example/a_(b)) and https://y.example."
    )
    assert as_plain_text("![](https://x.example/p.png) and <https://z.example/q> and a<br>b") == (
        "https://x.example/p.png and https://z.example/q and a b"
    )


def test_the_owners_answer_reads_clean() -> None:
    answer: str = (
        "Done. Here is what I found:\n\n## Summary\n- **Disk**: 120 GB free\n- **Memory**: 8 GB\n  - swap: 2 GB\n\n"
        "| App | CPU |\n|---|---|\n| Safari | 4% |\n\n> Note: numbers change.\n\n---\n\n1. Restart\n2. Check"
    )
    assert as_plain_text(answer) == (
        "Done. Here is what I found:\n\nSummary:\n• Disk: 120 GB free\n• Memory: 8 GB\n  ◦ swap: 2 GB\n\n"
        "App · CPU\nSafari · 4%\n\nNote: numbers change.\n\n1. Restart\n2. Check"
    )


def test_no_mark_of_markdown_is_left_in_an_answer_that_is_all_of_them() -> None:
    answer: str = "# H\n\n- a\n- b\n\n> q\n\n---\n\n| x | y |\n|---|---|\n| 1 | 2 |\n\n**b** _i_ ~~s~~ `c`"
    plain: str = as_plain_text(answer)
    for mark in ("#", "- ", "> ", "---", "|", "**", "~~", "`"):
        assert mark not in plain


def test_line_kinds_tell_prose_from_code_and_fences() -> None:
    lines: list[str] = ["a", "```py", "code", "```", "b", "~~~", "c", "~~~~", "d"]
    assert line_kinds(lines) == ["prose", "fence", "code", "fence", "prose", "fence", "code", "fence", "prose"]


def test_an_answer_that_fits_is_sent_whole_and_a_longer_one_is_cut_with_the_link() -> None:
    assert cut_with_link("short answer", 100, LINK) == "short answer"
    long: str = "word " * 1000
    cut: str = cut_with_link(long, 300, LINK)
    assert len(cut) <= 300
    assert cut.endswith(LINK)
    assert "The rest of the answer is in Dot" in cut
    assert cut.startswith("word word")


def test_a_cut_with_no_link_says_the_rest_is_in_dot_on_the_mac_and_names_no_address() -> None:
    """Without an address a phone can open (item 611) the line that ends a cut answer names no link."""
    assert cut_with_link("short answer", 100, None) == "short answer"
    cut: str = cut_with_link("word " * 1000, 300, None)
    assert len(cut) <= 300
    assert cut.endswith("… The rest of the answer is in Dot on your Mac.")
    assert "http" not in cut and cut.startswith("word word")


def test_a_cut_comes_at_a_line_break_when_one_is_close() -> None:
    lines: str = "\n".join(f"line number {number} of the answer" for number in range(100))
    cut: str = cut_with_link(lines, 400, LINK)
    head: str = cut.split("\n\n… The rest")[0]
    assert all(line.startswith("line number") and line.endswith("of the answer") for line in head.splitlines())


def test_one_line_joins_the_words_and_marks_a_cut() -> None:
    assert one_line("a\n\n  b   **c**", 40) == "a b c"
    cut: str = one_line("x" * 50, 20)
    assert len(cut) == 20 and cut.endswith("…")


LONG: str = (
    "Dot read the page twice, saved its tables as a file and wrote the three numbers that changed into the note."
)


def test_the_items_of_a_list_with_a_long_one_stand_apart() -> None:
    """A list whose item wraps on a phone's screen is one block of text there: its items stand apart, with a blank
    line before and after the list (item 627)."""
    markdown: str = f"What happened today:\n- {LONG}\n- A short one.\n- {LONG}\nThat is all."
    assert as_plain_text(markdown) == (
        f"What happened today:\n\n• {LONG}\n\n• A short one.\n\n• {LONG}\n\nThat is all."
    )
    numbered: str = f"1. {LONG}\n2. Two."
    assert as_plain_text(numbered) == f"1. {LONG}\n\n2. Two."


def test_a_list_of_short_items_stays_one_block() -> None:
    assert as_plain_text("Buy:\n- milk\n- bread\n- eggs\nThanks.") == "Buy:\n• milk\n• bread\n• eggs\nThanks."
    # Sixty characters of words are short, whatever the indent and the mark before them; a nested item of more is long.
    sixty: str = "x" * 60
    assert as_plain_text(f"- {sixty}\n- b\n    - {sixty}\n12. {sixty}") == f"• {sixty}\n• b\n  ◦ {sixty}\n12. {sixty}"
    assert as_plain_text(f"- a\n  - {sixty}y\n- b") == f"• a\n  ◦ {sixty}y\n\n• b"


def test_nested_items_and_an_items_further_words_stay_under_it_in_a_list_that_stands_apart() -> None:
    markdown: str = f"- {LONG}\n  - a nested one\n  - another\n- Second item\n  its words go on here\n- Third"
    assert as_plain_text(markdown) == (
        f"• {LONG}\n  ◦ a nested one\n  ◦ another\n\n• Second item\n  its words go on here\n\n• Third"
    )


def test_a_lists_first_item_stays_under_its_heading() -> None:
    assert as_plain_text(f"## Next\n- {LONG}\n- Two.") == f"Next:\n• {LONG}\n\n• Two."


def test_a_run_of_spaces_between_words_is_one_space_and_code_keeps_its_own() -> None:
    assert as_plain_text("One.  Two   three\t\tfour.") == "One. Two three four."
    assert as_plain_text("Run `a  b` now.\n```\nx   =  1\n```") == "Run a  b now.\nx   =  1"
    # The indent of a nested item is no run between words.
    assert as_plain_text("- one\n    - two  words") == "• one\n  ◦ two words"
