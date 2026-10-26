"""A step of the browser that sends or pays is judged by what it presses (item 682): a click by its element's name
whatever its role (a shop's ``div`` that pays is a ``generic`` with a pointer), and Enter or a line break in a text
field by the buttons near it that it may press, a composer's Send beside its box or its form's button. Before, a click
on an element of no known role was judged by nothing, and Enter in a field by the field's name alone."""

from dotagent.browser.safety import (
    assess_step,
    keys_go_to,
    parse_element,
    payment_steps,
    submits_field,
    submits_unnamed,
)
from dotagent.browser.snapshot import controls_near, parse_snapshot
from dotagent.browser.types import Step
from dotagent.policy.elements import Element

COMPOSER: str = """- main [ref=e1]:
  - list "Messages" [ref=e2]:
    - listitem [ref=e3]: Hello
  - generic [ref=e4]:
    - generic [ref=e5]:
      - textbox "Type a message" [ref=e6]
    - button "Send" [ref=e7]
- button "New chat" [ref=e8]"""

CHECKOUT: str = """- form "Checkout" [ref=e1]:
  - textbox "Promo code" [ref=e2]
  - button "Pay now" [ref=e3]
- searchbox "Search the shop" [ref=e4]
- button "Search" [ref=e5]"""


def _near(snapshot: str, ref: str) -> tuple[Element, ...]:
    lines = parse_snapshot(snapshot)
    return tuple(element for line in controls_near(lines, ref) if (element := parse_element(line)) is not None)


def test_a_click_is_judged_by_its_elements_name_whatever_its_role() -> None:
    paying: Element | None = parse_element("- generic [ref=e5] [cursor=pointer]: Pay now")
    for element in (paying, Element(role="generic", name="Pay now"), Element(role="img", name="Place order")):
        assessed = assess_step(Step(action="click", ref="e5"), element, None)
        assert "financial" in assessed.flags and assessed.risk == "high", (element, assessed)
    assert assess_step(Step(action="click", ref="e5"), Element(role="generic", name="Details"), None).flags == ()


def test_a_text_fields_value_never_names_it() -> None:
    """The review of item 682: what a field without a name holds is its value, which neither judges a step on it nor
    goes into its summary as its name."""
    for role in ("textbox", "searchbox", "combobox", "spinbutton"):
        assert parse_element(f"- {role} [ref=e7]: Pay now") == Element(role=role, name=""), role
    assert parse_element('- textbox "Note" [ref=e7]: Pay now') == Element(role="textbox", name="Note")


NESTED: str = """- generic [ref=e1]:
  - generic [ref=e2]:
    - spinbutton "Quantity" [ref=e3]
    - button "Calculate" [ref=e4]
  - button "Pay now" [ref=e5]"""


DEEP_FORM: str = """- form "Checkout" [ref=e1]:
  - generic [ref=e2]:
    - generic [ref=e3]:
      - generic [ref=e4]:
        - textbox "Promo code" [ref=e5]
  - button "Pay now" [ref=e6]"""


def test_the_buttons_of_the_form_count_however_deep_the_field_sits_in_it() -> None:
    """The code review of item 682: a form four levels above its field was past the levels looked at."""
    assert [element.name for element in _near(DEEP_FORM, "e5")] == ["Pay now"]


def test_a_harmless_button_beside_the_field_does_not_hide_the_one_that_pays_above_it() -> None:
    """The security review of item 682: the buttons of every level up to the form are those Enter may press, not the
    nearest level's alone; and Enter in a number field submits as in a text field."""
    assert [element.name for element in _near(NESTED, "e3")] == ["Calculate", "Pay now"]
    quantity: Element = Element(role="spinbutton", name="Quantity")
    pressed = assess_step(Step(action="press", ref="e3", key="Enter"), quantity, None, _near(NESTED, "e3"))
    assert "financial" in pressed.flags
    box: Element = Element(role="checkbox", name="I agree")
    assert submits_field(Step(action="press", ref="e3", key="Enter"), box)
    assert not submits_field(Step(action="type", ref="e3", text="x\n"), box)


def test_the_buttons_near_a_field_are_those_enter_may_press() -> None:
    assert [element.name for element in _near(COMPOSER, "e6")] == ["Send"]
    assert [element.name for element in _near(CHECKOUT, "e2")] == ["Pay now"]
    # A field in no form whose surroundings are the page itself has none near it.
    assert _near(CHECKOUT, "e4") == ()
    assert _near(COMPOSER, "e99") == ()


def test_enter_or_a_line_break_in_a_field_is_judged_by_what_it_presses() -> None:
    field: Element = Element(role="textbox", name="Type a message")
    near: tuple[Element, ...] = _near(COMPOSER, "e6")
    for step in (
        Step(action="press", ref="e6", key="Enter"),
        Step(action="press", ref="e6", key="Meta+Enter"),
        Step(action="type", ref="e6", text="See you at six\n"),
    ):
        assert submits_field(step, field)
        assessed = assess_step(step, field, None, near)
        assert {"external_send", "message_send"} <= set(assessed.flags), (step, assessed)
    typed = assess_step(Step(action="type", ref="e6", text="See you at six"), field, None, near)
    assert "message_send" not in typed.flags
    promo: Element = Element(role="textbox", name="Promo code")
    paying_step: Step = Step(action="press", ref="e2", key="Enter")
    paid = assess_step(paying_step, promo, None, _near(CHECKOUT, "e2"))
    assert "financial" in paid.flags
    assert payment_steps([(paying_step, promo, paid)], ()) == 1
    search = assess_step(Step(action="press", ref="e4", key="Enter"), Element(role="searchbox", name="Search"), None)
    assert search.flags == ()


def test_a_key_that_names_no_field_goes_where_the_batch_put_the_focus_or_asks_as_a_send() -> None:
    """The code review of item 682: ``[fill e6 "hello", press Enter]`` sends from the box the fill named, and Enter
    pressed where no field was named at all sends what is not known."""
    steps: list[Step] = [
        Step(action="fill", ref="e6", text="See you at six"),
        Step(action="press", key="Enter"),
        Step(action="click_at", x=10, y=10),
        Step(action="press", key="Enter"),
    ]
    # A click at a point puts the focus where Dot cannot tell: the Enter after it names no field.
    assert keys_go_to(steps, [step.ref for step in steps]) == ["e6", "e6", None, None]
    assert keys_go_to([Step(action="press", key="Enter")], [None]) == [None]
    # A Tab moves the focus where Dot cannot tell: the Enter after it names no field, and asks as a send.
    tabbed: list[Step] = [
        Step(action="fill", ref="e12", text="x"),
        Step(action="press", key="Tab"),
        Step(action="press", key="Enter"),
    ]
    assert keys_go_to(tabbed, [step.ref for step in tabbed]) == ["e12", "e12", None]
    # A hover or a scroll into view does not move the focus: the Enter after it is the box's (the sixth code review).
    hovered: list[Step] = [
        Step(action="fill", ref="e12", text="hello"),
        Step(action="hover", ref="e40"),
        Step(action="scroll_into_view", ref="e41"),
        Step(action="press", key="Enter"),
    ]
    assert keys_go_to(hovered, [step.ref for step in hovered]) == ["e12", "e40", "e41", "e12"]
    # A drag, or a Tab pressed on a named field, leaves the focus where Dot cannot tell.
    dragged: list[Step] = [
        Step(action="fill", ref="e12", text="x"),
        Step(action="drag", ref="e3", to_ref="e4"),
        Step(action="press", key="Enter"),
    ]
    assert keys_go_to(dragged, [step.ref for step in dragged])[-1] is None
    named_tab: list[Step] = [Step(action="press", ref="e12", key="Tab"), Step(action="press", key="Enter")]
    assert keys_go_to(named_tab, [step.ref for step in named_tab]) == ["e12", None]
    assert submits_unnamed(Step(action="press", key="Meta+Enter"))
    assert not submits_unnamed(Step(action="press", key="Tab"))
