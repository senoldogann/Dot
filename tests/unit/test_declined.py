"""A no is not asked again in the same run (item 456): the action is kept as its card showed it, a kind that says not
what it writes is never kept, nor a card that shows less than the whole action, a no holds until the owner writes
again, and the run is told how to go on."""

from dotagent.agent.declined import Declined, declined_key, refusal_text, still_declined
from dotagent.core.text import redact_secrets, truncate_middle, truncate_tail
from dotagent.desktop.render import describe_step
from dotagent.desktop.types import Step
from dotagent.mcp.types import McpTool, ToolHints
from dotagent.policy.types import ActionInfo, Amount
from dotagent.tools.mcp import classify_mcp_call

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


def test_a_kind_that_says_not_what_it_writes_is_never_kept_nor_a_card_cut_short() -> None:
    for kind in ("write", "browser_input", "desktop_input"):
        assert declined_key(CLICK.model_copy(update={"kind": kind})) is None, kind
    # Two posts whose first 300 characters are the same are not one action.
    assert declined_key(CLICK.model_copy(update={"summary": "post " + "x" * 300 + "…"})) is None


def test_a_card_that_shows_less_than_the_whole_action_is_never_kept() -> None:
    """The whole class (item 456's third review): wherever a card cut its text, told a text by its length, or masked a
    secret, two actions it cannot tell apart would be one, so none of them is kept — as the tools write their cards."""
    lookup: McpTool = McpTool(
        server="notes",
        remote_name="search",
        exposed_name="notes_search",
        description="Search the notes.",
        input_schema={},
        hints=ToolHints(read_only=False, destructive=None, open_world=None),
        startup_timeout_s=10.0,
        call_timeout_s=60.0,
    )
    # An MCP call's arguments cut in the middle of its card: the limit after the cut differs.
    for limit in (5, 50):
        mcp: ActionInfo = classify_mcp_call(lookup, {"query": "q" * 400, "limit": limit})
        assert "…" in mcp.summary and not mcp.summary.endswith("…"), mcp.summary
        assert declined_key(mcp) is None
    typed: str = describe_step(Step(action="type", ref="a3", text="rm -rf ~/work"))
    partial: tuple[str, ...] = (
        f"Terminal: {typed}",
        f"Run: {truncate_middle('x' * 400, 200)} (deletes files)",
        f"Read: {truncate_tail('y' * 400, 200)}",
        redact_secrets("curl -H 'Authorization: Bearer sk-ant-api03-abcdefghijklmnopqrstuvwxyz0123456789'"),
    )
    for summary in partial:
        assert declined_key(CLICK.model_copy(update={"kind": "shell", "summary": summary})) is None, summary
        assert declined_key(CLICK.model_copy(update={"kind": "shell", "target": summary})) is None, summary
    # A card that shows the whole action is kept.
    assert declined_key(CLICK.model_copy(update={"summary": "click e9 'Reply all'"})) is not None


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
