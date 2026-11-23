"""``resume_after`` (item 1352): a stream goes on after the later of ``after`` and the ``Last-Event-ID`` header, and a
header that is no number is no id."""

import pytest

from dotagent.server.stream import resume_after


@pytest.mark.parametrize(
    ("after", "header", "expected"),
    [
        (0, None, 0),
        (0, "", 0),
        (0, "42", 42),
        (50, "42", 50),
        (5, " 42 ", 42),
        (7, "abc", 7),
        (7, "-3", 7),
        (7, "4 2", 7),
        (7, "٣", 7),
        (0, "9" * 40, 0),
    ],
)
def test_the_later_of_the_query_and_the_header_wins(after: int, header: str | None, expected: int) -> None:
    assert resume_after(after, header) == expected
