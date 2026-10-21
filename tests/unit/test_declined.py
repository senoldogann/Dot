"""A no is not asked again in the same run (item 456): the action is kept as its card showed it, a kind that says not
what it writes is never kept, a no holds until the owner writes again, and the run is told how to go on."""

from dotagent.agent.declined import Declined, declined_key, refusal_text, still_declined
from dotagent.policy.types import ActionInfo, Amount

CLICK: ActionInfo = ActionInfo(
    tool="browser_act",
    kind="browser_click",
    risk="medium",
    summary="click e9 'Reply'",
    target="https://mail.example/inbox",
    flags=(),
)


def test_an_action_is_kept_as_its_card_showed_it() -> None:
    assert declined_key(CLICK) == declined_key(CLICK.model_copy())
    # An element keeps its ref while its role and name stay the same: another Reply button is another action.
    assert declined_key(CLICK.model_copy(update={"summary": "click e14 'Reply'"})) != declined_key(CLICK)
    assert declined_key(CLICK.model_copy(update={"target": "https://mail.example/sent"})) != declined_key(CLICK)
    paying: ActionInfo = CLICK.model_copy(update={"amount": Amount(value=40.0, currency="EUR")})
    assert declined_key(paying) != declined_key(
        paying.model_copy(update={"amount": Amount(value=400.0, currency="EUR")})
    )


def test_a_kind_that_says_not_what_it_writes_is_never_kept() -> None:
    for kind in ("write", "browser_input", "desktop_input"):
        assert declined_key(CLICK.model_copy(update={"kind": kind})) is None, kind


def test_a_no_holds_until_the_owner_writes_again() -> None:
    key: str | None = declined_key(CLICK)
    assert key is not None
    kept: dict[str, Declined] = {key: Declined(note="not that", spoken=1)}
    assert still_declined(kept, key, 1) == kept[key]
    assert still_declined(kept, key, 2) is None
    assert still_declined(kept, None, 1) is None
    assert still_declined({}, key, 1) is None


def test_the_run_is_told_the_no_stands_and_how_to_go_on() -> None:
    told: str = refusal_text(Declined(note="not that", spoken=0))
    assert "“not that”" in told and "until the user writes again" in told and "ask_user" in told
    assert "note" not in refusal_text(Declined(note="", spoken=0))
