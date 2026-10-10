"""The delta of two rendered page views: what the model reads after an act that changed something, or nothing."""

from dotagent.browser.snapshot import ViewDiff, diff_views, prefer_diff, render_diff

_BEFORE: str = '- heading "Shop"\n- button "Add" [ref=e1]\n- link "Cart (0)" [ref=e2]\n'
_AFTER: str = '- heading "Shop"\n- button "Add" [ref=e1]\n- link "Cart (1)" [ref=e2]\n'


def test_an_unchanged_page_comes_back_as_one_line_that_says_so() -> None:
    diff: ViewDiff = diff_views(_BEFORE, _BEFORE)
    assert (diff.added, diff.removed_refs, diff.unchanged) == ((), (), 3)
    assert prefer_diff(diff, _BEFORE)
    assert render_diff(diff) == "# no change since the last observation (3 lines unchanged); the refs still hold"


def test_a_changed_page_comes_back_as_its_changed_lines_with_the_counts() -> None:
    diff: ViewDiff = diff_views(_BEFORE, _AFTER)
    assert diff.unchanged == 2
    lines: list[str] = render_diff(diff).splitlines()
    assert lines[0].startswith("# changed since the last observation: 1 new/changed lines, 2 unchanged")
    assert lines[1:] == ['- link "Cart (1)" [ref=e2]']
