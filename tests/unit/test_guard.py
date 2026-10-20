"""The guard no rule turns off (item 427): a message sent for the user or a post in their name asks in every mode and
no rule that allows it answers for them; a yolo run leaves money, credentials, the guard and a pre-approved spend to the
user and asks before their secrets; and every classifier that knows a message goes out says so."""

from pathlib import Path

import pytest

from dotagent.desktop.safety import StepContext, assess_step
from dotagent.desktop.types import Step
from dotagent.policy.elements import Element, assessment_for, naming_reasons
from dotagent.policy.engine import asked_in_yolo, auto_review, decide, route_for
from dotagent.policy.shell import assess_applescript, assess_shell
from dotagent.policy.types import ActionInfo, ApprovalMode, Decision
from dotagent.store.models import Behavior, RuleRecord
from dotagent.tools.mcp import sends_messages

_HOME: Path = Path("/tmp/dot-tests/workspace")
MESSAGE: ActionInfo = ActionInfo(
    tool="apple_script",
    kind="app_control",
    risk="high",
    summary="Run applescript: send the reply",
    target="Mail",
    flags=("external_send", "message_send"),
)
POST: ActionInfo = ActionInfo(
    tool="browser_act",
    kind="browser_click",
    risk="medium",
    summary="Post the reply",
    target="https://social.example/thread",
    flags=("public_post",),
)
PAYMENT: ActionInfo = ActionInfo(
    tool="browser_act",
    kind="browser_click",
    risk="high",
    summary="Pay the invoice",
    target="shop.example",
    flags=("financial",),
)
SECRET: ActionInfo = ActionInfo(
    tool="read_file",
    kind="read",
    risk="high",
    summary="Read the SSH key",
    target="~/.ssh/id_ed25519",
    flags=("secret_access",),
)
DELETE: ActionInfo = ActionInfo(
    tool="shell", kind="shell", risk="high", summary="Run: rm -rf build", target="rm -rf build", flags=("destructive",)
)


def _rule(behavior: Behavior) -> RuleRecord:
    return RuleRecord(
        id="rule-1",
        position=0,
        enabled=True,
        behavior=behavior,
        tool_glob="*",
        match_regex="",
        description="the user's own answer",
        created_at=0.0,
    )


@pytest.mark.parametrize("action", [MESSAGE, POST])
@pytest.mark.parametrize("behavior", [None, "allow", "preapproved", "ask"])
def test_a_message_or_a_post_asks_whatever_a_rule_that_lets_it_go_says(
    action: ActionInfo, behavior: Behavior | None
) -> None:
    rules: list[RuleRecord] = [] if behavior is None else [_rule(behavior)]
    decision: Decision = decide(action, "task", False, "aggressive", rules, None)
    assert (decision.verdict, decision.source) == ("ask", "guard")
    assert decision.reason.endswith("whatever your rules say")


def test_a_rule_that_hands_a_message_off_is_stricter_and_holds() -> None:
    decision: Decision = decide(MESSAGE, "task", False, "aggressive", [_rule("handoff")], None)
    assert (decision.verdict, decision.source) == ("handoff", "rule")


@pytest.mark.parametrize("mode", ["supervised", "ask", "yolo"])
def test_money_and_messages_are_the_users_in_yolo_too(mode: ApprovalMode) -> None:
    for action in (MESSAGE, POST, PAYMENT):
        assert route_for(decide(action, "task", False, "aggressive", [], None), mode) == "user", action.summary


def test_the_review_model_answers_a_message_in_auto_but_never_money() -> None:
    assert route_for(decide(MESSAGE, "task", False, "aggressive", [], None), "auto") == "reviewer"
    assert route_for(decide(PAYMENT, "task", False, "aggressive", [], None), "auto") == "user"


def test_yolo_still_waives_what_the_preset_asks() -> None:
    decision: Decision = decide(DELETE, "task", False, "balanced", [], None)
    assert (decision.verdict, decision.source) == ("ask", "preset")
    assert route_for(decision, "yolo") == "waived"


def test_what_the_always_on_review_refuses_elsewhere_a_yolo_run_asks() -> None:
    refusal: Decision | None = auto_review(SECRET)
    assert refusal is not None and refusal.verdict == "deny"
    asked: Decision = asked_in_yolo(SECRET)
    assert (asked.verdict, asked.source) == ("ask", "auto_review")
    assert (
        asked.reason
        == "in yolo Dot asks you before it reads where secrets are kept or sends secrets out (secret_access)"
    )


def test_every_classifier_that_knows_a_message_goes_out_says_so() -> None:
    # A Send button, by its name and by the typed judge's reason alike.
    assert "message_send" in assessment_for(naming_reasons(Element(role="button", name="Send"))).flags
    assert "message_send" in assessment_for(["send"]).flags
    assert (
        "message_send" in assess_applescript('tell application "Mail" to send theMessage', _HOME, _HOME, (), ()).flags
    )
    assert "message_send" in assess_shell("echo hi | mail -s Hi ada@example.com", _HOME, _HOME, (), ()).flags
    assert "message_send" in assess_shell("sendmail ada@example.com < note.txt", _HOME, _HOME, (), ()).flags
    assert "message_send" not in assess_shell("mail", _HOME, _HOME, (), ()).flags
    messaging: StepContext = StepContext(role="messaging", app="Messages", element=None, secure=False, line=None)
    assert "message_send" in assess_step(Step(action="key", key="return"), messaging, _HOME, (), ()).flags
    assert "message_send" in assess_step(Step(action="type", text="See you\n"), messaging, _HOME, (), ()).flags
    for name in ("slack_post_message", "send_email", "reply_to_thread", "postMessage", "create_post", "add_comment"):
        assert sends_messages(name), name
    for name in ("get_post", "list_messages", "search_messages", "create_issue", "create_draft", "postgres_query"):
        assert not sends_messages(name), name
