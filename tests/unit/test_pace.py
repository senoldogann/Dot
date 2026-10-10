"""What a click on a social platform is, from its label, and the pace of a site: its windows, its spacing and the actions
it counts."""

from dotagent.config import PaceConfig
from dotagent.policy.elements import Element, StepAssessment, assessment_for, naming_reasons
from dotagent.policy.pace import PacedAction, pace_refusal, paced_action, spacing_wait
from dotagent.policy.types import ActionInfo, Flag

NOW: float = 1_800_000_000.0
PACE: PaceConfig = PaceConfig(
    public_per_hour=2, public_per_day=3, social_per_hour=0, social_per_day=0, public_spacing_s=20.0
)
X_PUBLIC: PacedAction = PacedAction(host="x.com", pace="public")


def _flags(label: str) -> tuple[str, tuple[Flag, ...]]:
    found: StepAssessment = assessment_for(naming_reasons(Element(role="button", name=label)))
    return found.risk, found.flags


def test_reposts_quotes_follows_and_likes_are_seen_in_english_turkish_and_finnish_and_plain_text_is_not() -> None:
    assert _flags("Repost") == _flags("5 reposts. Repost") == _flags("Quote") == ("medium", ("public_post",))
    assert _flags("Julkaise") == ("medium", ("public_post",))
    assert _flags("Yeniden gönder") == ("medium", ("external_send", "public_post"))
    assert _flags("Lähetä") == ("medium", ("external_send",))
    for social in ("Follow @dot", "12 Likes. Like", "Takip et", "Beğen", "Seuraa", "Tykkää", "Subscribe"):
        assert _flags(social) == ("low", ("social",)), social
    for plain in ("Get a quote", "Posts you might like", "Reposted by Dot", "Following @dot", "Likes"):
        assert _flags(plain) == ("low", ()), plain


def test_a_site_takes_its_limit_in_a_sliding_hour_and_day_and_says_when_the_next_one_is_allowed() -> None:
    assert pace_refusal(PACE, X_PUBLIC, [NOW - 7_200, NOW - 100], NOW) is None
    hour: str | None = pace_refusal(PACE, X_PUBLIC, [NOW - 7_200, NOW - 600, NOW - 100], NOW)
    assert hour is not None and hour.startswith("Pace for x.com: 2 of 2 public actions in the last hour")
    day: str | None = pace_refusal(PACE, X_PUBLIC, [NOW - 9_000, NOW - 7_200, NOW - 5_000], NOW)
    assert day is not None and day.startswith("Pace for x.com: 3 of 3 public actions in the last day")
    # 0 removes a limit.
    assert pace_refusal(PACE, PacedAction(host="x.com", pace="social"), [NOW - 1.0] * 500, NOW) is None
    assert spacing_wait(PACE, X_PUBLIC, [NOW - 100, NOW - 5], NOW) == 15.0
    assert spacing_wait(PACE, X_PUBLIC, [NOW - 100], NOW) == 0.0


def test_the_pace_counts_public_and_social_browser_actions_by_their_site() -> None:
    def action(kind: str, target: str, flags: tuple[Flag, ...]) -> ActionInfo:
        return ActionInfo.model_validate(
            {"tool": "t", "kind": kind, "risk": "medium", "summary": "s", "target": target, "flags": flags}
        )

    assert paced_action(action("browser_click", "https://www.x.com/home", ("public_post", "social"))) == X_PUBLIC
    assert paced_action(action("browser_click", "https://x.com/dot", ("social",))) == PacedAction(
        host="x.com", pace="social"
    )
    assert paced_action(action("browser_click", "https://x.com/home", ())) is None
    assert paced_action(action("browser_click", "about:blank", ("public_post",))) is None
    assert paced_action(action("message", "https://x.com/home", ("external_send",))) is None
