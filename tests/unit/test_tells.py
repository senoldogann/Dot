"""What the owner does with what Dot tells them (item 1743): the count of the answers, apart from the store."""

import pytest

from dotagent.notify.tells import (
    TELL_WINDOW_S,
    OwnerAct,
    TellResponse,
    TellRow,
    TellSent,
    counts_of,
    run_of_route,
    tell_rows,
    tell_state,
)

T0: float = 1_000_000.0


def _tell(tell_id: str, route: str, kind: str = "watcher", subject: str = "w1", ts: float = T0) -> TellSent:
    return TellSent.model_validate({"tell_id": tell_id, "ts": ts, "kind": kind, "subject": subject, "route": route})


def _answer(tell_id: str, response: str, after: float) -> TellResponse:
    return TellResponse.model_validate({"tell_id": tell_id, "ts": T0 + after, "response": response, "via": "app"})


@pytest.mark.parametrize(
    ("route", "run"),
    [("#/runs/run_1", "run_1"), ("#/runs/run_1/steps", None), ("#/runs/", None), ("#/settings/schedules", None)],
)
def test_only_the_route_of_one_chats_run_opens_a_chat(route: str, run: str | None) -> None:
    assert run_of_route(route) == run


def test_a_tell_is_acted_on_opened_dismissed_ignored_or_still_waiting() -> None:
    chat: str = "thr_1"
    tell: TellSent = _tell("t1", "#/runs/run_1")
    late: float = TELL_WINDOW_S + 60
    # The owner wrote in the chat within the half hour: acted on, whatever else they did.
    wrote: list[OwnerAct] = [OwnerAct(thread_id=chat, ts=T0 + 120)]
    assert tell_state(tell, [_answer("t1", "opened", 30)], chat, wrote, T0 + late) == "acted"
    # A message in another chat, or after the half hour, is not an answer to this tell.
    elsewhere: list[OwnerAct] = [OwnerAct(thread_id="thr_2", ts=T0 + 120), OwnerAct(thread_id=chat, ts=T0 + late)]
    assert tell_state(tell, [], chat, elsewhere, T0 + late + 10) == "ignored"
    assert tell_state(tell, [_answer("t1", "opened", 90)], chat, [], T0 + late) == "opened"
    assert tell_state(tell, [_answer("t1", "dismissed", 5)], chat, [], T0 + late) == "dismissed"
    # Opened after the dismissal: the better answer stands. Opened after the half hour: the owner's, not the tell's.
    both: list[TellResponse] = [_answer("t1", "dismissed", 5), _answer("t1", "opened", 60)]
    assert tell_state(tell, both, chat, [], T0 + late) == "opened"
    assert tell_state(tell, [_answer("t1", "opened", late)], chat, [], T0 + late + 5) == "ignored"
    assert tell_state(tell, [], chat, [], T0 + 100) == "pending"


def test_a_tell_that_opens_no_chat_cannot_be_seen_opened_unless_it_was_dismissed() -> None:
    tell: TellSent = _tell("t2", "#/settings/schedules")
    assert tell_state(tell, [], None, [], T0 + 10_000) == "unmeasured"
    assert tell_state(tell, [_answer("t2", "dismissed", 4)], None, [], T0 + 10_000) == "dismissed"
    # The run of a tell whose chat was deleted opens nothing either.
    assert tell_state(_tell("t3", "#/runs/gone"), [], None, [], T0 + 10_000) == "unmeasured"


def test_the_share_that_was_read_is_of_the_tells_that_have_an_answer_and_a_signal() -> None:
    counts = counts_of(["acted", "opened", "ignored", "ignored", "dismissed", "pending", "unmeasured"])
    assert (counts.sent, counts.acted, counts.opened, counts.dismissed, counts.ignored) == (7, 1, 1, 1, 2)
    assert (counts.pending, counts.unmeasured) == (1, 1)
    assert counts.precision == 2 / 5
    assert counts_of(["pending", "unmeasured"]).precision is None
    assert counts_of([]).precision is None


def test_the_rows_are_one_for_each_kind_and_watcher_in_the_order_of_the_kinds() -> None:
    now: float = T0 + 10 * TELL_WINDOW_S
    tells: list[TellSent] = [
        _tell("a", "#/runs/r_a", kind="watcher", subject="w2"),
        _tell("b", "#/runs/r_b", kind="brief", subject=""),
        _tell("c", "#/runs/r_c", kind="watcher", subject="w1"),
        _tell("d", "#/runs/r_d", kind="watcher", subject="w1"),
        _tell("e", "#/settings/phone", kind="system", subject=""),
    ]
    threads: dict[str, str] = {"r_a": "t_a", "r_b": "t_b", "r_c": "t_c", "r_d": "t_d"}
    answers: list[TellResponse] = [_answer("c", "opened", 20)]
    acts: list[OwnerAct] = [OwnerAct(thread_id="t_b", ts=T0 + 50)]
    rows: tuple[TellRow, ...] = tell_rows(tells, answers, threads, acts, now)
    assert [(row.kind, row.subject) for row in rows] == [
        ("brief", ""),
        ("watcher", "w1"),
        ("watcher", "w2"),
        ("system", ""),
    ]
    by_key: dict[tuple[str, str], TellRow] = {(row.kind, row.subject): row for row in rows}
    assert by_key[("brief", "")].counts.acted == 1
    assert (by_key[("watcher", "w1")].counts.opened, by_key[("watcher", "w1")].counts.ignored) == (1, 1)
    assert by_key[("watcher", "w2")].counts.precision == 0.0
    assert by_key[("system", "")].counts.unmeasured == 1
