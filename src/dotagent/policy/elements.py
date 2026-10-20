"""Name-based risk rules shared by the browser and the desktop: what a control's label says about a click or a
keystroke.

A label comes from a web page or an application, so it is untrusted: it can only make a step look riskier than the
action alone, never safer. The policy engine decides what the resulting risk and flags mean.
"""

import re
from collections.abc import Collection, Sequence

from dotagent.core.types import FrozenModel
from dotagent.policy.types import Flag, Risk

_KEY_LIKE: re.Pattern[str] = re.compile(
    r"sk-[A-Za-z0-9_\-]{16,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}"
    r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
)
_FINANCIAL_ACTION: re.Pattern[str] = re.compile(
    r"(?i)\b(?:buy now|buy it now|purchase|place (?:your |my )?order|complete (?:your |my )?(?:order|purchase|payment)"
    r"|confirm (?:your |my )?(?:order|purchase|payment)|pay now|pay with|make (?:a )?payment|submit payment|donate"
    r"|send money|wire transfer|sat[ıi]n al|[öo]demeyi tamamla|sipari[şs]i? (?:ver|onayla|tamamla)"
    r"|ba[ğg][ıi][şs] yap)\b"
    r"|^pay$|^[öo]de$"
)
_DESTRUCTIVE_ACTION: re.Pattern[str] = re.compile(
    r"(?i)\b(?:delete|remove|erase|discard|destroy|wipe|deactivate|terminate|permanently|empty (?:the )?trash"
    r"|close (?:my |your )?account|cancel (?:my |your )?(?:subscription|membership|order|account)"
    r"|sil|kald[ıi]r|hesab[ıi] (?:kapat|sil)|aboneli[ğg]i iptal|[çc][öo]p sepetini bo[şs]alt)\b"
)
_SEND_ACTION: re.Pattern[str] = re.compile(r"(?i)\b(?:send|reply|forward|g[öo]nder|yan[ıi]tla|l[äa]het[äa])\b")
# A quote counts only at the start of a label or of a sentence in it ("Quote", "12 Quotes. Quote"), so that "Get a
# quote" stays a plain click.
_POST_ACTION: re.Pattern[str] = re.compile(
    r"(?i)\b(?:post|publish|tweet|retweet|repost|share|comment|payla[şs]|yay[ıi]nla|yeniden g[öo]nder|julkaise)\b"
    r"|(?:^|\.\s+)(?:quote|al[ıi]nt[ıi](?:la)?)\b"
)
# Follows, likes and subscriptions, in the same place of a label ("Follow @dot", "12 Likes. Like"), so that a link
# called "Posts you might like" is no follow or like.
_SOCIAL_ACTION: re.Pattern[str] = re.compile(
    r"(?i)(?:^|\.\s+)(?:follow|unfollow|like|unlike|subscribe|unsubscribe|connect|takip et|takibi b[ıi]rak|be[ğg]en"
    r"|be[ğg]enmekten vazge[çc]|abone ol|seuraa|tykk[äa][äa])\b"
)
_CREDENTIAL_FIELD: re.Pattern[str] = re.compile(
    r"(?i)\b(?:password|passcode|passphrase|pin(?: code)?|otp|one[- ]time|verification code|authenticator|2fa|mfa"
    r"|recovery code|[şs]ifre|parola|do[ğg]rulama kodu|g[üu]venlik kodu)\b"
)
_PAYMENT_FIELD: re.Pattern[str] = re.compile(
    r"(?i)\b(?:card number|credit card|debit card|cvv|cvc|security code|expir\w*|iban|routing number"
    r"|account number|kart numaras[ıi]|son kullanma)\b"
)

# The flags of each reason: a send is a message sent for the owner too (item 429), which no rule of theirs lets through.
_FLAGS_OF_REASON: dict[str, tuple[Flag, ...]] = {
    "credential": ("credential",),
    "payment": ("financial",),
    "financial": ("financial",),
    "destructive": ("destructive",),
    "send": ("external_send", "message_send"),
    "post": ("public_post",),
    "social": ("social",),
}
_HIGH_REASONS: frozenset[str] = frozenset({"credential", "payment", "financial"})
# A follow or a like is outward-facing but small: it keeps the risk of a plain click.
_LOW_REASONS: frozenset[str] = frozenset({"social"})
_RISK_ORDER: tuple[Risk, ...] = ("safe", "low", "medium", "high")


class Element(FrozenModel):
    """A control as the model sees it: its role and its accessible name."""

    role: str
    name: str


class StepAssessment(FrozenModel):
    risk: Risk
    flags: tuple[Flag, ...]
    reasons: tuple[str, ...]


def looks_like_key(text: str) -> bool:
    """Whether ``text`` contains something that looks like an API key or a private key."""
    return _KEY_LIKE.search(text) is not None


def asks_to_pay(text: str) -> bool:
    """Whether ``text`` is a call to buy, pay or donate."""
    return _FINANCIAL_ACTION.search(text) is not None


def typing_reasons(element: Element | None, text: str | None) -> list[str]:
    """Reasons a keystroke into ``element`` carrying ``text`` is sensitive (secret or payment field, key-like text)."""
    reasons: list[str] = []
    if element is not None:
        if _CREDENTIAL_FIELD.search(element.name):
            reasons.append("credential")
        if _PAYMENT_FIELD.search(element.name):
            reasons.append("payment")
    if text is not None and looks_like_key(text):
        reasons.append("credential")
    return list(dict.fromkeys(reasons))


def clicking_reasons(element: Element | None, clickable_roles: Collection[str]) -> list[str]:
    """Reasons a click on ``element`` is sensitive; only elements of ``clickable_roles`` are judged by their name."""
    if element is None or element.role not in clickable_roles:
        return []
    return naming_reasons(element)


def naming_reasons(element: Element | None) -> list[str]:
    """Reasons acting on ``element`` is sensitive, from its name whatever its role: buying, deleting, sending, posting,
    following or liking."""
    if element is None:
        return []
    reasons: list[str] = []
    for label, pattern in (
        ("financial", _FINANCIAL_ACTION),
        ("destructive", _DESTRUCTIVE_ACTION),
        ("send", _SEND_ACTION),
        ("post", _POST_ACTION),
        ("social", _SOCIAL_ACTION),
    ):
        if pattern.search(element.name):
            reasons.append(label)
    return reasons


def quiet_assessment() -> StepAssessment:
    """A step that changes nothing."""
    return StepAssessment(risk="safe", flags=(), reasons=())


def assessment_for(reasons: Sequence[str]) -> StepAssessment:
    """Risk and flags of an acting step: low without reasons or with only a follow or a like, medium with others, high
    when money or secrets are involved."""
    flags: tuple[Flag, ...] = tuple(dict.fromkeys(flag for reason in reasons for flag in _FLAGS_OF_REASON[reason]))
    if any(reason in _HIGH_REASONS for reason in reasons):
        return StepAssessment(risk="high", flags=flags, reasons=tuple(reasons))
    if any(reason not in _LOW_REASONS for reason in reasons):
        return StepAssessment(risk="medium", flags=flags, reasons=tuple(reasons))
    return StepAssessment(risk="low", flags=flags, reasons=tuple(reasons))


def combine_assessments(assessments: Sequence[StepAssessment]) -> StepAssessment:
    """The batch is as risky as its riskiest step and carries every flag of every step."""
    if not assessments:
        return quiet_assessment()
    risk: Risk = max((a.risk for a in assessments), key=_RISK_ORDER.index)
    flags: tuple[Flag, ...] = tuple(dict.fromkeys(flag for a in assessments for flag in a.flags))
    reasons: tuple[str, ...] = tuple(dict.fromkeys(reason for a in assessments for reason in a.reasons))
    return StepAssessment(risk=risk, flags=flags, reasons=reasons)
