"""The ablation of a skill or a note: its arms, the blind order and how a pick is unblinded, the noise band of the two
identical arms and the verdict beside it."""

from dotagent.evals.ablation import (
    WITH,
    WITHOUT,
    WITHOUT_AGAIN,
    Finding,
    Pairing,
    Pick,
    findings,
    left_shown_first,
    part_variants,
    sections_of,
    shown_pick,
    skill_variants,
    unblinded,
)
from dotagent.evals.types import SkillSeed

SKILL: SkillSeed = SkillSeed(
    name="memo", description="Write a memo.", body="# memo\n\n## Steps\n\n1. Write it.\n\n## Style\n\nShort lines.\n"
)


def test_a_skill_has_with_without_twice_and_one_arm_per_part_left_out() -> None:
    assert [heading for heading, _text in sections_of(SKILL.body)] == ["Steps", "Style"]
    assert [variant.name for variant in skill_variants(SKILL)] == [WITH, WITHOUT, WITHOUT_AGAIN]
    parts = part_variants(SKILL)
    assert [variant.name for variant in parts] == ["without part 'Steps'", "without part 'Style'"]
    assert "Short lines" in parts[0].skills[0].body and "Write it" not in parts[0].skills[0].body


def test_the_judge_never_knows_which_arm_and_the_pick_is_unblinded() -> None:
    orders: set[bool] = {left_shown_first(f"real-{n}", WITH, WITHOUT) for n in range(20)}
    assert orders == {True, False}
    assert left_shown_first("real-1", WITH, WITHOUT) == left_shown_first("real-1", WITH, WITHOUT)
    assert unblinded("first", True) == "left" and unblinded("first", False) == "right"
    assert unblinded("second", False) == "left" and unblinded("same", True) == "same"
    assert shown_pick({"better": "second", "why": "complete"}) == "second"


def _pairs(left: str, right: str, picks: list[Pick]) -> list[Pairing]:
    return [
        Pairing(case_id=f"real-{n}", left=left, right=right, pick=pick, left_tokens=200, right_tokens=100)
        for n, pick in enumerate(picks)
    ]


def test_a_variant_helps_only_above_the_noise_band_of_two_identical_arms() -> None:
    noise: list[Pairing] = _pairs(WITHOUT, WITHOUT_AGAIN, ["left", "right", "same", "left"])
    found: tuple[Finding, ...] = findings([*_pairs(WITH, WITHOUT, ["left", "left", "left", "same"]), *noise])
    assert [(item.variant, item.verdict, item.noise, item.tokens_change) for item in found] == [
        (WITH, "helps", 0.25, 100.0)
    ]
    within: tuple[Finding, ...] = findings([*_pairs(WITH, WITHOUT, ["left", "right", "same", "same"]), *noise])
    assert within[0].verdict == "within_noise"
    few: tuple[Finding, ...] = findings(_pairs(WITH, WITHOUT, ["left", "left"]))
    assert few[0].verdict == "too_few"
