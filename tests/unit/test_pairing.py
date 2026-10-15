"""Pairing a phone (item 267): a code of eight characters of Crockford's base32, read back as a person types it, good
for ten minutes and once, used up by five wrong tries; a code typed in other letters matches nothing."""

from dotagent.server.pairing import (
    ALPHABET,
    CODE_TRIES,
    CODE_TTL_S,
    PairingCode,
    Redeemed,
    new_code,
    redeem,
    shown_code,
    typed_code,
)

NOW: float = 1_000_000.0
GOOD: PairingCode = PairingCode(code="7KQ2M9XD", expires_at=NOW + CODE_TTL_S, tries_left=CODE_TRIES)


def test_a_code_is_eight_characters_of_the_alphabet_shown_in_two_groups() -> None:
    code: PairingCode = new_code(NOW)
    assert len(code.code) == 8 and set(code.code) <= set(ALPHABET)
    assert (code.expires_at, code.tries_left) == (NOW + CODE_TTL_S, CODE_TRIES)
    assert shown_code(code) == f"{code.code[:4]}-{code.code[4:]}"
    assert len({new_code(NOW).code for _ in range(50)}) == 50


def test_a_code_is_read_as_a_person_types_it() -> None:
    assert typed_code(" 7kq2-m9xd ") == "7KQ2M9XD"
    assert typed_code("o1l0-IOLO") == "01101010"


def test_a_code_matches_once_and_only_while_it_is_good() -> None:
    assert redeem(GOOD, "7kq2 m9xd", NOW) == Redeemed(matched=True, left=None)
    assert redeem(None, "7KQ2-M9XD", NOW) == Redeemed(matched=False, left=None)
    assert redeem(GOOD, "7KQ2-M9XD", NOW + CODE_TTL_S) == Redeemed(matched=False, left=None)


def test_five_wrong_tries_use_a_code_up() -> None:
    left: PairingCode | None = GOOD
    for tries in range(CODE_TRIES - 1, 0, -1):
        outcome: Redeemed = redeem(left, "AAAA-AAAA", NOW)
        assert outcome == Redeemed(matched=False, left=PairingCode(GOOD.code, GOOD.expires_at, tries))
        left = outcome.left
    assert redeem(left, "AAAA-AAAA", NOW) == Redeemed(matched=False, left=None)


def test_a_code_typed_in_other_letters_matches_nothing() -> None:
    assert redeem(GOOD, "7KQ2-M9XĐ", NOW).matched is False
    assert redeem(GOOD, "çöğü-şıİ1", NOW).left == PairingCode(GOOD.code, GOOD.expires_at, CODE_TRIES - 1)
