"""The marks of the runs that the owner's emergency stop asked other processes to end (item 553): written with the ask
to stop, read where the ask is answered, taken away once it was, and let go after a day when nobody answered."""

import json

import pytest

from dotagent.agent.manager import _emergency_marks, _marked, _unmarked

_DAY_S: float = 86_400.0


@pytest.mark.parametrize("raw", [None, "", "not json", "[1, 2]", '"run_a"', "3"])
def test_what_is_not_a_set_of_marks_reads_as_none(raw: str | None) -> None:
    assert _emergency_marks(raw) == {}


def test_a_mark_that_is_no_time_is_left_out() -> None:
    assert _emergency_marks('{"run_a": 5, "run_b": "x", "run_c": 7.5, "run_d": null}') == {"run_a": 5.0, "run_c": 7.5}


def test_runs_are_marked_with_when_they_were_asked_and_a_mark_nobody_answered_lapses_after_a_day() -> None:
    first: str = _marked(None, ["run_a"], 1000.0)
    assert json.loads(first) == {"run_a": 1000.0}
    second: str = _marked(first, ["run_b", "run_c"], 1010.0)
    assert json.loads(second) == {"run_a": 1000.0, "run_b": 1010.0, "run_c": 1010.0}
    # A day and a few seconds later the first is let go, and the one asked again is asked again.
    third: str = _marked(second, ["run_c", "run_d"], 1000.0 + _DAY_S + 5)
    assert json.loads(third) == {"run_b": 1010.0, "run_c": 1000.0 + _DAY_S + 5, "run_d": 1000.0 + _DAY_S + 5}


def test_the_marks_of_the_runs_that_were_answered_are_taken_away_and_the_others_stay() -> None:
    marks: str = _marked(None, ["run_a", "run_b", "run_c"], 1000.0)
    assert json.loads(_unmarked(marks, ["run_a", "run_c"])) == {"run_b": 1000.0}
    assert json.loads(_unmarked(None, ["run_a"])) == {}
