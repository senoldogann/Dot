"""What a step sends out of the Mac and whether it carries a secret there, and which steps a yolo run that read outside
content checks with the review model before it does them (item 525)."""

import pytest

from dotagent.policy.egress import carries_secret, sends_out
from dotagent.policy.engine import checked_when_tainted
from dotagent.policy.types import ActionInfo, ActionKind, Flag

# Made-up values in the forms of a key and of a secret Dot holds; none of them is a real one.
_KEY_FORM: str = "sk-test-0123456789abcdefghij"
_HELD: str = "correct-horse-battery-staple"


def _step(kind: ActionKind, flags: tuple[Flag, ...] = (), target: str = "https://example.net/") -> ActionInfo:
    return ActionInfo(tool="t", kind=kind, risk="medium", summary="a step", target=target, flags=flags)


@pytest.mark.parametrize(
    ("step", "sends"),
    [
        (_step("network"), True),
        (_step("browser_navigate"), True),
        (_step("browser_input"), True),
        (_step("message"), True),
        (_step("mcp"), True),
        (_step("mcp_read"), True),
        (_step("shell", ("upload",)), True),
        (_step("browser_click", ("public_post",)), True),
        (_step("read"), False),
        (_step("write"), False),
        (_step("shell"), False),
        (_step("browser_read"), False),
    ],
)
def test_a_step_sends_out_by_its_kind_or_by_a_flag_of_sending(step: ActionInfo, sends: bool) -> None:
    assert sends_out(step) is sends


def test_a_step_that_sends_out_carries_a_secret_held_or_one_in_a_key_s_form_anywhere_in_its_arguments() -> None:
    fetch: ActionInfo = _step("network")
    assert carries_secret(fetch, {"url": f"https://example.net/?k={_KEY_FORM}"}, ())
    assert carries_secret(fetch, {"body": {"lines": ["hello", f"token: {_HELD}"]}}, (_HELD,))
    # Read as the tool reads it, not escaped as JSON: a secret with a quote in it is still found.
    quoted: str = 'pass"word-1234'
    assert carries_secret(fetch, {"text": f"the secret is {quoted}"}, (quoted,))
    assert carries_secret(_step("network", target=f"https://example.net/{_HELD}"), {}, (_HELD,))
    assert carries_secret(
        fetch, {"key": "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----"}, ()
    )
    # Nothing secret, a held value too short to stand for one, and a step that sends nothing out.
    assert not carries_secret(fetch, {"url": "https://example.net/?q=weather"}, (_HELD,))
    assert not carries_secret(fetch, {"text": "short"}, ("short",))
    assert not carries_secret(_step("write"), {"content": _KEY_FORM}, (_HELD,))


@pytest.mark.parametrize(
    ("flags", "checked"),
    [
        (("external_send", "message_send"), True),
        (("public_post",), True),
        (("upload",), True),
        (("financial",), True),
        (("credential",), True),
        (("persistence",), True),
        (("secret_access",), True),
        (("exfiltration",), True),
        (("install",), True),
        (("self_change",), True),
        (("vault_fill",), True),
        (("destructive", "outside_workspace"), True),
        (("social",), False),
        (("destructive",), False),
        (("outside_workspace",), False),
        ((), False),
    ],
)
def test_a_yolo_run_that_read_outside_content_checks_what_a_planted_instruction_is_after(
    flags: tuple[Flag, ...], checked: bool
) -> None:
    assert checked_when_tainted(_step("shell", flags)) is checked
