"""A question that asks the user to hand over a secret is refused and sent to the card (item 1289): a question's text
is written in the chat and kept with the run, and the code the run needs (the one Stripe sends to the owner's phone)
belongs on the card of ``ask_secret`` alone."""

import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from dotagent.agent.approvals import ApprovalOutcome
from dotagent.agent.verify import new_facts
from dotagent.server import UI_SOURCE
from dotagent.tools.context import ToolContext
from dotagent.tools.meta import ASK_SECRET_TOOL, ASK_USER_TOOL, build_meta_tools, secret_asked

_STRIPE_QUESTION: str = (
    "Stripe'a girişte telefona gelen doğrulama kodunu istiyor; bana hiç ulaşmadı (chat'e kod düşmemiş, e-postada da "
    "yok). Kodu buraya yazar mısın? (6 haneli, 10 dakika geçerli)"
)


def _context(runtime: object) -> ToolContext:
    return ToolContext(
        browser=cast(Any, None),
        run_id="run-1",
        agent_id=None,
        thread_id="thread-1",
        mode="task",
        approvals="yolo",
        questions="leave",
        workspace=Path(),
        gate=cast(Any, SimpleNamespace(add_instruction=lambda text: None)),
        cancel=asyncio.Event(),
        output_chars=4_000,
        facts=new_facts,
        runtime=cast(Any, runtime),
    )


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        (_STRIPE_QUESTION, ("a one-time code", "CODE")),
        ("Telefonuna gelen doğrulama kodunu yazar mısın?", ("a one-time code", "CODE")),
        ("What is the verification code on your phone?", ("a one-time code", "CODE")),
        ("Which password belongs to the admin account?", ("a password", "PASSWORD")),
        ("şifreni yazar mısın?", ("a password", "PASSWORD")),
        ("Salasanan annatko minulle?", ("a password", "PASSWORD")),
        ("Can I have the verification code from your phone?", ("a one-time code", "CODE")),
        ("May I have your Stripe password?", ("a password", "PASSWORD")),
        ("What password do I use for Stripe?", ("a password", "PASSWORD")),
        ("Doğrulama kodunu yazar mısın, girişi tamamlayayım.", ("a one-time code", "CODE")),
        ("Şifreni bilsem giriş yapardım, söyler misin?", ("a password", "PASSWORD")),
        ("Paste the API key into the box, please", ("a key or a token", "TOKEN")),
        ("Salasana puhelime tuli, annatko sen?", ("a password", "PASSWORD")),
        ("What's the password on the router?", ("a password", "PASSWORD")),
        ("Telefonuna gelen onay kodunu gönderir misin?", ("a one-time code", "CODE")),
    ],
)
def test_a_question_that_asks_for_a_secret_is_read_as_one(question: str, expected: tuple[str, str]) -> None:
    assert secret_asked(question) == expected


@pytest.mark.parametrize(
    "question",
    [
        "Kodu nereden aldın?",
        "Kodu GitHub'a göndereyim mi?",
        "Should I use the promo code SAVE10 at checkout?",
        "Should I write the code in Python or Rust?",
        "What is the error code you see?",
        "Did the verification code arrive on your phone?",
        "Which option do you prefer, the phone or the bank?",
        "Which 2FA app should I use?",
        "Did the password expire on the router?",
        "Is the code field on the page already filled?",
        "Which port should I note?",
        "Which version of the tool is installed?",
        "Which token limit should I set?",
        "Is the token count still under the cap?",
        "Where does the password reset link go?",
    ],
)
def test_a_question_about_a_secret_that_asks_for_no_value_is_no_secret_asked_for(question: str) -> None:
    assert secret_asked(question) is None


async def test_ask_user_refuses_to_ask_for_a_code_and_sends_the_run_to_the_card() -> None:
    left: list[tuple[object, ...]] = []

    async def leave(*args: object, **kwargs: object) -> None:
        left.append(args)

    tool = next(tool for tool in build_meta_tools() if tool.spec.name == ASK_USER_TOOL)
    context = _context(SimpleNamespace(approvals=SimpleNamespace(leave=leave), notify=_no_notify))
    prepared = await tool.prepare({"question": _STRIPE_QUESTION}, context)

    result = await prepared.run(context)

    assert "asks the user for a one-time code" in result.text
    assert "ask_secret" in result.text
    assert "name='CODE'" in result.text
    assert not left


async def _no_notify(title: str, message: str) -> None:
    return


async def test_a_code_the_user_types_on_the_card_reaches_the_run_as_their_own_words() -> None:
    """The card's way out for a code too short to keep (item 1073) is a note; the code in it is the user's own words,
    which is what the page's one-time-code field takes outside yolo (item 1289)."""
    instructions: list[str] = []

    async def request(*args: object, **kwargs: object) -> ApprovalOutcome:
        return ApprovalOutcome(approval_id="apr-1", status="denied", decided_by=UI_SOURCE, note="4816")

    async def leave(*args: object, **kwargs: object) -> None:
        return

    tool = next(tool for tool in build_meta_tools() if tool.spec.name == ASK_SECRET_TOOL)
    context = _context(SimpleNamespace(approvals=SimpleNamespace(request=request, leave=leave), notify=_no_notify))
    context = replace(
        context,
        thread_id="thread-1289-card",
        questions="wait",
        gate=SimpleNamespace(add_instruction=instructions.append),
    )
    prepared = await tool.prepare(
        {"name": "STRIPE_CODE", "label": "The code Stripe sent your phone", "why": "to sign in"}, context
    )

    result = await prepared.run(context)

    assert "4816" in result.text
    assert instructions == ["4816"]


def test_ask_secret_takes_one_name_or_two_to_four_fields() -> None:
    """Item 2040: one secret takes name; things that go together take fields, 2 to 4, never both."""
    from pydantic import ValidationError

    from dotagent.tools.meta import AskSecretArgs

    AskSecretArgs(name="DEMO_TOKEN", label="Demo", why="To read the visits.")
    pair = [
        {"name": "ASC_ISSUER_ID", "label": "Issuer ID", "kind": "text"},
        {"name": "ASC_KEY", "label": "Key", "kind": "file"},
    ]
    AskSecretArgs.model_validate({"label": "App Store Connect API key", "why": "To upload.", "fields": pair})
    for wrong in (
        {"label": "x", "why": "y", "name": "A_TOKEN", "fields": pair},
        {"label": "x", "why": "y", "fields": pair[:1]},
        {"label": "x", "why": "y", "fields": pair * 3},
        {"label": "x", "why": "y"},
    ):
        with pytest.raises(ValidationError):
            AskSecretArgs.model_validate(wrong)


def test_a_one_name_card_with_a_long_title_still_has_its_field() -> None:
    """Item 2040: a one-name call whose label is 81 to 120 characters (valid before fields came) builds its card."""
    from dotagent.tools.meta import AskSecretArgs, _card_fields  # pyright: ignore[reportPrivateUsage]

    args = AskSecretArgs(name="DEMO_TOKEN", label="L" * 100, why="To read the visits.")

    fields = [(field.name, len(field.label), field.kind) for field in _card_fields(args)]

    assert fields == [("DEMO_TOKEN", 100, "secret")]
