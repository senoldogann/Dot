"""The options of collapsed dropdowns: folding them onto the dropdown's line, listing them within a budget, matching."""

from dotagent.browser.snapshot import (
    OptionEntry,
    closest_first,
    entries_of,
    fold_options,
    has_option,
    parse_snapshot,
    render_lines,
    render_options,
    viewport_filter,
)
from dotagent.browser.types import Dropdown, DropdownOption

# Written the way Playwright writes it: a collapsed <select> lists its options without a ref and without a size, a name
# with a colon quotes the whole line, and the flags of an option come in the order disabled, selected.
_SNAPSHOT: str = """\
- generic [active] [ref=e1] [box=8,8,1264,90]:
  - combobox "Odd" [ref=e3] [box=40,9,136,19]:
    - option "Say \\"hi\\"" [box=0,0,0,0]
    - 'option "Mr: Smith" [box=0,0,0,0]'
    - option "Pick one…" [disabled] [selected] [box=0,0,0,0]
    - option "[selected] trick" [box=0,0,0,0]
    - option [box=0,0,0,0]
  - listbox "Toppings" [ref=e8] [box=40,40,100,40]:
    - option "Cheese" [ref=e9] [box=40,40,100,20]
    - option "Ham" [selected] [ref=e10] [box=40,60,100,20]
  - button "Go" [ref=e4] [box=180,8,34,21]
"""


def option(label: str, value: str, selected: bool) -> DropdownOption:
    return DropdownOption(label=label, value=value, selected=selected, disabled=False)


def test_the_options_of_a_collapsed_dropdown_move_onto_its_line_and_options_with_refs_stay() -> None:
    folded = fold_options(parse_snapshot(_SNAPSHOT))
    view: str = render_lines(viewport_filter(folded, 1280, 860, 0))
    assert view.splitlines() == [
        '- combobox "Odd" [ref=e3]: options "Say \\"hi\\"", "Mr: Smith", "Pick one…" [selected] [disabled], '
        '"[selected] trick", ""',
        '- listbox "Toppings" [ref=e8]:',
        '  - option "Cheese" [ref=e9]',
        '  - option "Ham" [selected] [ref=e10]',
        '- button "Go" [ref=e4]',
    ]


def test_a_choice_that_does_not_fit_is_named_after_the_count() -> None:
    entries: list[OptionEntry] = [
        OptionEntry(name=f'"Number {n}"', selected=n == 250, disabled=False) for n in range(300)
    ]
    listed: str = render_options(entries, 100)
    assert listed.startswith('options "Number 0", "Number 1", "Number 2"')
    assert listed.endswith(', … and 292 more (selected: "Number 250")')
    assert render_options(entries[:3], 100) == 'options "Number 0", "Number 1", "Number 2"'
    assert render_options([], 100) == "no options"


def test_options_are_matched_like_playwright_and_the_closest_come_first() -> None:
    dropdown: Dropdown = Dropdown(
        options=(option("Standard (3-5 days)", "std", True), option("Express  (next\u200b day)", "exp", False))
    )
    assert has_option(dropdown, "std")
    assert has_option(dropdown, "Express (next day)")
    assert not has_option(dropdown, "express (next day)")
    assert [found.value for found in closest_first(dropdown.options, "next day delivery")] == ["exp", "std"]
    entries: tuple[OptionEntry, ...] = entries_of((option("", "x", False), dropdown.options[1]))
    assert [entry.name for entry in entries] == ['"x"', '"Express (next day)"']
