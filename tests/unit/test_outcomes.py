"""What a step of a batch came to, beyond its being sent (item 1145): the rules that turn an observation into a
check, and the words a result says them with. Done is not verified, and a check never carries what a field holds."""

import pytest
from pydantic import ValidationError

from dotagent.browser.session import effect_of_expected
from dotagent.browser.types import PAGE_UNCHANGED
from dotagent.browser.types import Step as BrowserStep
from dotagent.core.outcomes import (
    OutcomeCheck,
    SightEnd,
    StepReport,
    echoes_written,
    gate_lost,
    holds_back,
    judge_added,
    judge_chosen,
    judge_replaced,
    judge_sight,
    judge_ticked,
    not_looked_for,
    render_outcomes,
    render_step,
    still_on_its_way,
    url_shows,
)
from dotagent.tools.browser import ActArgs as BrowserActArgs
from dotagent.tools.browser import guesses_to_the_call

_LOOK: str = "look again"
_NOT_SEEN: tuple[SightEnd, ...] = ("timeout", "still", "dialog", "no_time")


def _report(outcome: str, dispatched: str, checks: tuple[OutcomeCheck, ...]) -> StepReport:
    return StepReport.model_validate(
        {
            "index": 2,
            "description": "click e9",
            "outcome": outcome,
            "detail": "",
            "dispatched": dispatched,
            "checks": checks,
        }
    )


def test_a_field_that_holds_what_was_written_is_verified_as_written_or_as_the_page_formats_it() -> None:
    assert judge_replaced("", "Ada", "Ada").status == "verified"
    assert judge_replaced("old", " Ada \r\n", "Ada").evidence == "as written"
    formatted: OutcomeCheck = judge_replaced("", "(555) 123-4567", "5551234567")
    assert (formatted.status, formatted.evidence) == ("verified", "formatted by the page")
    # Filling a field with what it holds already leaves it holding it: the state asked for is so.
    assert judge_replaced("Ada", "Ada", "Ada").status == "verified"


def test_a_field_the_page_adds_to_holds_the_text_and_a_short_text_is_not_found_by_chance() -> None:
    """Item 1149: an address field put ``https://`` before what a run wrote, and the click after it was held back."""
    scheme: OutcomeCheck = judge_replaced("", "https://gistsmith.example", "gistsmith.example")
    assert scheme.status == "verified"
    assert scheme.evidence == "the page added to it: 25 characters where 17 were written"
    assert judge_replaced("", "100 kg", "100").status == "verified"
    assert judge_replaced("", "+358 40 123 4567", "0401234567").status == "uncertain"
    assert judge_replaced("", "+358 40 123 4567", "401234567").status == "verified"
    # One letter ends many words: a text that short has to be the field's own.
    assert judge_replaced("", "banana", "a").status == "uncertain"
    # In the middle of other text, it is not what was written with more around it.
    assert judge_replaced("", "see gistsmith.example now", "gistsmith.example").status == "uncertain"
    # Written again into the field that holds it with its scheme, the text is held: the field is as it was, as one
    # filled again with the very text it holds is (the review of this item: the retry the owner's run made).
    again: OutcomeCheck = judge_replaced("https://gistsmith.example", "https://gistsmith.example", "gistsmith.example")
    assert again.status == "verified"
    # A field that is as it was and holds other text did not take what was written.
    assert judge_replaced("Ada Lovelace", "Ada Lovelace", "Grace Hopper").status == "contradicted"


def test_a_page_that_puts_back_a_value_ending_in_the_text_is_taken_for_one_that_added_to_it() -> None:
    """The price of item 1149, chosen and not overlooked. A page that answers ``Lovelace`` by showing ``Ada Lovelace``
    again does what one that puts ``https://`` before an address does: it shows the text with more before it. The
    two cannot be told apart by the field, so both are held, and the check says how many characters the field holds
    against how many were written, which is what gives the first one away."""
    put_back: OutcomeCheck = judge_replaced("Ada Lovelace", "Ada Lovelace", "Lovelace")
    assert put_back.status == "verified"
    assert put_back.evidence == "the page added to it: 12 characters where 8 were written"


def test_a_field_that_is_as_it_was_contradicts_the_write_and_one_that_changed_otherwise_is_uncertain() -> None:
    kept: OutcomeCheck = judge_replaced("fixed", "fixed", "mine")
    assert kept.status == "contradicted" and "did not take the text" in kept.reason
    cut: OutcomeCheck = judge_replaced("", "ABCDE", "ABCDEFGH")
    assert cut.status == "uncertain"
    # What the field holds is told by its length alone: a password typed into a plain field is no secret field.
    assert cut.evidence == "it holds 5 characters where 8 were written"
    assert "ABCDE" not in cut.model_dump_json()


def test_text_put_in_at_the_caret_counts_only_when_it_is_there_once_more_than_before() -> None:
    assert judge_added("", "Hels", "Hels").status == "verified"
    assert judge_added("to Helsinki", "to Helsinki, Helsinki", "Helsinki").status == "verified"
    assert judge_added("", "(555) 123-4567", "5551234567").evidence == "formatted by the page"
    # The field held the text already and is as it was: it did not take it a second time.
    again: OutcomeCheck = judge_added("Helsinki", "Helsinki", "Helsinki")
    assert again.status == "contradicted" and "before typing again" in again.reason
    other: OutcomeCheck = judge_added("", "Hel", "Helsinki")
    assert other.status == "uncertain" and "Hel" not in other.evidence


def test_keys_that_reach_their_field_later_are_never_contradicted() -> None:
    late: OutcomeCheck = still_on_its_way(judge_added("", "", "letters"), 1.0)
    assert late.status == "uncertain"
    assert "1 s after the keys were sent" in late.evidence and "before typing again" in late.reason
    took: OutcomeCheck = judge_added("", "letters", "letters")
    assert still_on_its_way(took, 0.1) is took


def test_a_box_and_a_dropdown_are_judged_by_what_they_show_after_the_step() -> None:
    assert judge_ticked(True, True).status == "verified"
    back: OutcomeCheck = judge_ticked(True, False)
    assert (back.status, back.evidence) == ("contradicted", "it is clear after the step")
    assert judge_chosen(True, False).status == "verified"
    assert judge_chosen(False, True).status == "contradicted"
    assert judge_chosen(False, False).status == "uncertain"


@pytest.mark.parametrize("end", _NOT_SEEN)
def test_what_was_expected_and_not_seen_is_uncertain_and_never_invites_a_blind_repeat(end: SightEnd) -> None:
    missed: OutcomeCheck = judge_sight("the expected text shows", end, 3.0, _LOOK)
    assert missed.status == "uncertain"
    assert missed.reason == "the step was sent once: look again before sending it again"


def test_what_was_expected_and_seen_is_verified() -> None:
    seen: OutcomeCheck = judge_sight("the expected text shows", "seen", 1.24, _LOOK)
    assert (seen.status, seen.evidence, seen.reason) == ("verified", "seen after 1.2 s", "")


def test_an_address_shows_a_part_in_its_path_and_not_in_what_a_query_carries() -> None:
    assert url_shows("https://shop.example/order/confirmed?id=7", "/order/confirmed")
    assert not url_shows("https://shop.example/login?next=/checkout", "/checkout")
    assert url_shows("https://shop.example/login?next=/checkout", "?next=/checkout")
    assert url_shows("https://shop.example/page#done", "#done")


def test_a_text_the_batch_writes_cannot_be_what_a_step_expects_to_see() -> None:
    assert echoes_written("ada lovelace", ["Ada  Lovelace"])
    assert echoes_written("Lovelace", ["Ada Lovelace"])
    assert not echoes_written("Welcome, Ada Lovelace", ["Ada Lovelace"])
    # The call is taken all the same (item 1164): refused, it cost a round trip of the model, and came back with
    # nothing to expect. The session sends the step and says that what it named was not looked for.
    taken: BrowserActArgs = BrowserActArgs.model_validate(
        {
            "steps": [
                {"action": "fill", "ref": "e5", "text": "Ada Lovelace"},
                {"action": "click", "ref": "e9", "expect_text": "Lovelace"},
            ]
        }
    )
    assert taken.steps[1].expect_text == "Lovelace"
    assert not_looked_for("") == ""
    assert not_looked_for("the page showed those words already before the step") == (
        "what it expected was not looked for (the page showed those words already before the step)"
    )
    assert gate_lost(2).startswith("what step 2 expected could not be looked for, so the steps after it were not run")


def test_a_guess_a_step_carries_is_the_calls_and_none_is_dropped() -> None:
    """Item 1164, from traced runs: a guess in the model's own words given to a step (``expect``) was refused whole,
    a round trip of the model for a meaning that was plain. It is the call's, after the call's own."""
    fill: dict[str, object] = {"action": "fill", "ref": "e5", "text": "lamp"}
    click: dict[str, object] = {"action": "click", "ref": "e6"}
    guessed: dict[str, object] = {**click, "expect": "the results list"}
    given: dict[str, object] = {"steps": [fill, guessed], "retry": False}
    assert guesses_to_the_call(given) == {"steps": [fill, click], "retry": False, "expect": "the results list"}
    # The arguments given are left as they were, and a call with no such step is given back as it is.
    assert given == {"steps": [fill, guessed], "retry": False} and guessed["expect"] == "the results list"
    plain: dict[str, object] = {"steps": [fill, click]}
    assert guesses_to_the_call(plain) is plain and guesses_to_the_call("nope") == "nope"
    # After the call's own, each kept and none twice.
    assert guesses_to_the_call({"expect": "a list", "steps": [guessed, {**fill, "expect": "a list"}]}) == {
        "steps": [click, fill],
        "expect": "a list; the results list",
    }
    # Through the tool's arguments, beside item 1161's reading of words written beside the steps: a step that only
    # looks is left out first, the words are the last step's, and the guess is the call's.
    args: BrowserActArgs = BrowserActArgs.model_validate(
        {"steps": [fill, guessed, {"action": "snapshot"}], "expect_text": "Brass desk lamp"}
    )
    assert (len(args.steps), args.steps[1].expect_text, args.expect) == (2, "Brass desk lamp", "the results list")


def test_a_step_takes_exact_words_to_expect_and_not_the_calls_free_guess() -> None:
    step: BrowserStep = BrowserStep.model_validate({"action": "click", "ref": "e9", "expect_url": "/done"})
    assert step.expects() and step.acts_on_what_was_left()
    with pytest.raises(ValidationError, match="expect belongs to the call"):
        BrowserStep.model_validate({"action": "click", "ref": "e9", "expect": "the order confirmation"})
    # A word of two letters is in too many others to tell anything.
    with pytest.raises(ValidationError):
        BrowserStep.model_validate({"action": "click", "ref": "e9", "expect_text": "ok"})


def test_which_steps_wait_for_a_look_after_a_check_that_is_not_verified() -> None:
    writing: list[BrowserStep] = [
        BrowserStep(action="fill", ref="e5", text="Ada"),
        BrowserStep(action="type", ref="e5", text="Ada"),
        BrowserStep(action="hover", ref="e5"),
    ]
    assert not any(step.acts_on_what_was_left() for step in writing)
    acting: list[BrowserStep] = [
        BrowserStep(action="click", ref="e9"),
        BrowserStep(action="press", key="Enter"),
        # A box or a dropdown whose change sends its form acts on what the fields hold.
        BrowserStep(action="check", ref="e7"),
        BrowserStep(action="select", ref="e8", text="Pro"),
        BrowserStep(action="type", ref="e5", text="Ada\n"),
    ]
    assert all(step.acts_on_what_was_left() for step in acting)


def test_a_steps_line_says_whether_it_was_sent_and_what_was_checked_of_it() -> None:
    assert render_step(_report("done", "sent", ()), "") == "  2. click e9 — done [not checked]"
    seen: OutcomeCheck = judge_sight("the expected text shows", "seen", 0.4, _LOOK)
    assert render_step(_report("done", "sent", (seen,)), "") == (
        "  2. click e9 — done [verified: the expected text shows; seen after 0.4 s]"
    )
    missed: OutcomeCheck = judge_sight("the expected text shows", "timeout", 10.0, _LOOK)
    assert render_step(_report("done", "sent", (missed,)), "").endswith(
        "[uncertain: the expected text shows; not seen within 10.0 s — the step was sent once: look again before "
        "sending it again]"
    )
    assert render_step(_report("failed", "not_sent", ()), "stale ref") == "  2. click e9 — failed (not sent): stale ref"
    assert " — failed (may have been sent): timed out" in render_step(_report("failed", "unknown", ()), "timed out")
    assert " — failed (sent): " in render_step(_report("failed", "sent", (judge_ticked(True, False),)), "put back")
    # A step that did not run is neither sent nor checked, and says neither.
    assert render_step(_report("skipped", "not_sent", ()), "") == "  2. click e9 — skipped"


def test_the_line_above_a_result_counts_what_was_verified_and_what_was_only_sent() -> None:
    seen: OutcomeCheck = judge_sight("the expected text shows", "seen", 0.4, _LOOK)
    missed: OutcomeCheck = judge_sight("the expected text shows", "timeout", 10.0, _LOOK)
    reports: list[StepReport] = [
        _report("done", "sent", (seen,)),
        _report("done", "sent", (seen, missed)),
        _report("done", "sent", ()),
        _report("failed", "sent", (judge_ticked(True, False),)),
        _report("skipped", "not_sent", ()),
    ]
    assert render_outcomes(reports) == "outcomes: 2 verified, 1 contradicted, 1 uncertain, 1 not checked"
    assert render_outcomes([_report("skipped", "not_sent", ())]) == "outcomes: none"
    assert holds_back((seen, missed)) and not holds_back((seen,)) and not holds_back(())


def test_the_report_of_a_step_that_named_what_it_expects_does_not_say_to_reach_the_element_another_way() -> None:
    advice: str = (
        ". Something in front may have taken the click, or the control may wait for another event: look at the page, "
        "close what is in front, or reach the element with browser_eval and its ref"
    )
    unchanged: str = f"{PAGE_UNCHANGED} (no new content, request, address or keyboard focus){advice}"
    missed: OutcomeCheck = judge_sight("the expected text shows", "still", 3.0, _LOOK)
    seen: OutcomeCheck = judge_sight("the expected text shows", "seen", 1.2, _LOOK)
    assert effect_of_expected(unchanged, (missed,)) == unchanged.removesuffix(advice)
    # What came later than the half second the page was watched for is no page that stayed as it was.
    assert effect_of_expected(unchanged, (seen,)) == ""
    assert effect_of_expected("the page says: Saved", (seen,)) == "the page says: Saved"
