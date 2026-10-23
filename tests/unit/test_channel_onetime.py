"""The code the owner types back to pair a channel (item 572): six digits, ten minutes, five tries."""

from dotagent.channels.onetime import CODE_DIGITS, CODE_TRIES, CODE_TTL_S, new_code, redeem, shown_code, typed_digits

NOW: float = 5_000.0


def test_a_code_is_six_digits_good_for_ten_minutes_and_five_tries() -> None:
    code = new_code(NOW)
    assert len(code.code) == CODE_DIGITS == 6 and code.code.isdigit()
    assert (code.expires_at, code.tries_left) == (NOW + CODE_TTL_S, CODE_TRIES) == (NOW + 600.0, 5)
    assert shown_code(code) == f"{code.code[:3]} {code.code[3:]}"


def test_the_codes_made_are_not_the_same_each_time() -> None:
    assert len({new_code(NOW).code for _ in range(20)}) > 1


def test_what_a_person_types_is_read_by_its_digits() -> None:
    assert typed_digits(" 482-913 ") == "482913"
    code = new_code(NOW)
    assert redeem(code, f"{code.code[:3]} {code.code[3:]}", NOW + 1).matched


def test_a_match_uses_the_code_up() -> None:
    code = new_code(NOW)
    outcome = redeem(code, code.code, NOW + 1)
    assert outcome.matched and outcome.left is None


def test_a_wrong_try_takes_one_try_and_the_fifth_uses_the_code_up() -> None:
    code = new_code(NOW)
    wrong: str = "000000" if code.code != "000000" else "111111"
    left = code
    for tries in range(CODE_TRIES - 1, 0, -1):
        outcome = redeem(left, wrong, NOW + 1)
        assert not outcome.matched and outcome.left is not None and outcome.left.tries_left == tries
        left = outcome.left
    last = redeem(left, wrong, NOW + 1)
    assert not last.matched and last.left is None
    # The right code after that is no use: there is none left to match.
    assert not redeem(last.left, code.code, NOW + 2).matched


def test_a_code_that_ran_out_matches_nothing() -> None:
    code = new_code(NOW)
    assert not redeem(code, code.code, NOW + CODE_TTL_S).matched
    assert not redeem(None, "123456", NOW).matched
