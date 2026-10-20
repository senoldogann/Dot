"""What a click on a social platform is, from its label, and the pace of a site: its windows, its spacing, the actions
it counts, and the batches it keeps to one public or social step (item 266)."""

from dotagent.config import PaceConfig
from dotagent.policy.elements import Element, StepAssessment, assessment_for, naming_reasons
from dotagent.policy.pace import PacedAction, pace_refusal, paced_action, paced_in_batch, spacing_wait
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
    assert _flags("Yeniden gönder") == ("medium", ("external_send", "message_send", "public_post"))
    assert _flags("Lähetä") == ("medium", ("external_send", "message_send"))
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


def test_a_batch_is_kept_to_one_public_or_social_step_only_by_a_pace_set_for_a_class_it_carries() -> None:
    unset: PaceConfig = PaceConfig(
        public_per_hour=0, public_per_day=0, social_per_hour=0, social_per_day=0, public_spacing_s=0.0
    )
    assert paced_in_batch(unset, ["social", "social", "social"]) == []
    assert paced_in_batch(unset, ["public", "public", "social"]) == []
    # PACE limits public actions and leaves social ones free.
    assert paced_in_batch(PACE, ["social", "social", "social"]) == []
    assert paced_in_batch(PACE, ["public", "social"]) == ["public"]
    assert paced_in_batch(PACE, ["public"]) == []
    spaced: PaceConfig = unset.model_copy(update={"public_spacing_s": 5.0})
    assert paced_in_batch(spaced, ["public", "public"]) == ["public"]
    followed: PaceConfig = unset.model_copy(update={"social_per_day": 100})
    assert paced_in_batch(followed, ["social", "social"]) == ["social"]
    assert paced_in_batch(followed, ["public", "public"]) == []
