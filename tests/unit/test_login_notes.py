"""The notes of a login whose password Dot replaced (items 1290 and 1294): each earlier password is a line, the oldest
go first when the notes would pass their limit, and the new header never goes."""

from dotagent.tools.browser import _replaced_notes
from dotagent.vault.items import MOST_NOTES_CHARS

HEADER: str = "Made by Dot for a new account on 2026-11-21."


def test_the_earlier_passwords_of_the_old_notes_are_kept_and_the_one_that_stood_is_added() -> None:
    first: str = "Made by Dot for a new account on 2026-11-01.\nReplaced on 2026-11-02, its earlier password: p1\n"
    notes: str = _replaced_notes(HEADER, first + "Replaced on 2026-11-03, its earlier password: p2", "p3", "2026-11-21")
    assert notes.splitlines() == [
        HEADER,
        "Replaced on 2026-11-02, its earlier password: p1",
        "Replaced on 2026-11-03, its earlier password: p2",
        "Replaced on 2026-11-21, its earlier password: p3",
    ]


def test_notes_of_the_first_replace_that_kept_its_line_after_the_header_are_read_too() -> None:
    old: str = "Made by Dot for a new account on 2026-11-01. Replaced on 2026-11-02, its earlier password: p1"
    notes: str = _replaced_notes(HEADER, old, "p2", "2026-11-21")
    assert notes.count("its earlier password:") == 2 and "p1" in notes and notes.startswith(HEADER)


def test_a_login_with_no_earlier_password_gets_one_line() -> None:
    assert _replaced_notes(HEADER, "Made by Dot for a new account on 2026-11-01.", "p1", "2026-11-21").splitlines() == [
        HEADER,
        "Replaced on 2026-11-21, its earlier password: p1",
    ]


def test_past_the_limit_the_oldest_lines_go_first_and_the_header_and_the_newest_stay() -> None:
    notes: str = HEADER
    for index in range(40):
        notes = _replaced_notes(HEADER, notes, f"password-{index:02d}-" + "x" * 8, f"2026-11-{index % 28 + 1:02d}")
        assert len(notes) <= MOST_NOTES_CHARS
        assert notes.startswith(HEADER) and f"password-{index:02d}-" in notes
    assert "password-00-" not in notes and "password-38-" in notes
    # The lines that stay are the newest, in order.
    kept: list[int] = [int(line.split("password-")[1][:2]) for line in notes.splitlines()[1:]]
    assert kept == sorted(kept) and kept[-1] == 39 and len(kept) > 5
