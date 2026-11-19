"""The ledger of a continuing piece of work (item 1227), as pure changes of its block: kept in the record beside the
owner's own text, an action begun before it goes out and never sent twice, an uncertain result never taken for a
failure, outcomes apart from actions and dated, and the ways of acting compared from what was kept."""

import pytest

from dotagent.pipeline.ledger import (
    EMPTY,
    END_MARK,
    START_MARK,
    ClientChange,
    Ledger,
    LedgerFormatError,
    LedgerRefusal,
    begin,
    compare,
    end,
    note_outcome,
    parse,
    pending,
    summary,
    upsert_client,
    written,
)

# A record as the owner keeps one: their own words and tables, in Turkish, with no block of Dot's yet.
OWNERS_RECORD: str = """# Asiakasputki — örnek

Kaynak liste: `leads/list.md`

## Gönderildi — 29.10.2026

| # | İşletme | E-posta | Durum |
|---|---|---|---|
| 1 | Örnek Leipomo | myynti@example.fi | Gönderildi |

**Senol'un kararı: hatırlatma yok.**
"""
NEW: ClientChange = ClientChange(
    name="Örnek Leipomo", contact="myynti@example.fi", status=None, expecting=None, next_step=None, next_at=None
)
NOTHING: ClientChange = ClientChange(name=None, contact=None, status=None, expecting=None, next_step=None, next_at=None)


def _one_client() -> Ledger:
    return upsert_client(EMPTY, "ornek-leipomo", NEW)


def test_a_record_without_a_block_reads_empty_and_gets_one_at_its_end_with_its_own_text_untouched() -> None:
    assert parse(OWNERS_RECORD) == EMPTY
    text: str = written(OWNERS_RECORD, _one_client())
    assert text.startswith(OWNERS_RECORD)
    assert text.count(START_MARK) == 1 and text.rstrip().endswith(END_MARK)
    assert parse(text) == _one_client()


def test_a_rewrite_changes_the_block_alone_and_keeps_what_the_owner_wrote_around_it() -> None:
    first: str = written(OWNERS_RECORD, _one_client())
    edited: str = first + "\n## Sonra\n\nSahibin yeni notu.\n"
    again: str = written(
        edited, begin(parse(edited), "ornek-leipomo", "first-mail", "subject-problem", "2026-11-19 10:00")
    )
    assert again.startswith(OWNERS_RECORD)
    assert again.endswith("\n## Sonra\n\nSahibin yeni notu.\n")
    assert parse(again).actions[0].state == "begun"


def test_cells_keep_pipes_and_lose_line_breaks() -> None:
    ledger: Ledger = upsert_client(
        EMPTY,
        "pipe-oy",
        ClientChange(
            name="Pipe | Oy", contact="a@b.fi", status=None, expecting="a\nreply", next_step=None, next_at=None
        ),
    )
    kept = parse(written("", ledger)).clients[0]
    assert (kept.name, kept.expecting) == ("Pipe | Oy", "a reply")


def test_an_action_is_never_begun_twice_while_its_result_is_unseen_and_never_sent_twice() -> None:
    ledger: Ledger = begin(_one_client(), "ornek-leipomo", "first-mail", "", "2026-11-19 10:00")
    # The run broke off here: the next one reads the file again and finds the action begun, not missing.
    reread: Ledger = parse(written(OWNERS_RECORD, ledger))
    assert [action.key for action in pending(reread)] == ["ornek-leipomo/first-mail"]
    with pytest.raises(LedgerRefusal, match="nobody has seen what came of it"):
        begin(reread, "ornek-leipomo", "first-mail", "", "2026-11-19 11:00")
    sent: Ledger = end(reread, "ornek-leipomo/first-mail", "sent", "In Sent at 10:01", "2026-11-19 11:05")
    assert pending(sent) == ()
    assert (sent.clients[0].last_action, sent.clients[0].status) == ("first-mail", "contacted")
    with pytest.raises(LedgerRefusal, match="it is not sent again"):
        begin(sent, "ornek-leipomo", "first-mail", "", "2026-11-20 09:00")
    # A further mail is an action of its own name.
    assert begin(sent, "ornek-leipomo", "reminder-1", "", "2026-11-26 09:00").actions[-1].state == "begun"


def test_an_uncertain_send_stays_in_the_way_until_it_is_looked_at_and_only_a_failed_one_is_tried_again() -> None:
    ledger: Ledger = begin(_one_client(), "ornek-leipomo", "first-mail", "", "2026-11-19 10:00")
    unsure: Ledger = end(ledger, "ornek-leipomo/first-mail", "uncertain", "The page timed out after Send", "10:02")
    with pytest.raises(LedgerRefusal, match="uncertain"):
        begin(unsure, "ornek-leipomo", "first-mail", "", "2026-11-19 12:00")
    failed: Ledger = end(
        unsure, "ornek-leipomo/first-mail", "failed", "Not in Sent at 12:10; draft still open", "12:10"
    )
    retried: Ledger = begin(failed, "ornek-leipomo", "first-mail", "", "2026-11-19 12:15")
    assert (retried.actions[0].state, retried.actions[0].tries) == ("begun", 2)
    assert retried.clients[0].last_action == ""


def test_an_end_needs_what_showed_it_and_ends_an_action_once() -> None:
    ledger: Ledger = begin(_one_client(), "ornek-leipomo", "first-mail", "", "2026-11-19 10:00")
    with pytest.raises(LedgerRefusal, match="say what showed it"):
        end(ledger, "ornek-leipomo/first-mail", "failed", "  ", "10:01")
    with pytest.raises(LedgerRefusal, match="begin it with pipeline_begin"):
        end(ledger, "ornek-leipomo/offer", "sent", "In Sent", "10:01")
    sent: Ledger = end(ledger, "ornek-leipomo/first-mail", "sent", "In Sent at 10:01", "10:01")
    with pytest.raises(LedgerRefusal, match="ended sent"):
        end(sent, "ornek-leipomo/first-mail", "failed", "Bounce", "10:05")


def test_a_client_the_owner_held_back_gets_no_action() -> None:
    held: Ledger = upsert_client(
        _one_client(),
        "ornek-leipomo",
        ClientChange(name=None, contact=None, status="do-not-contact", expecting=None, next_step=None, next_at=None),
    )
    with pytest.raises(LedgerRefusal, match="do-not-contact"):
        begin(held, "ornek-leipomo", "reminder-1", "", "2026-11-19 10:00")


def test_a_client_is_named_by_a_plain_id_and_a_new_one_needs_its_name() -> None:
    with pytest.raises(LedgerRefusal, match="no client id"):
        upsert_client(EMPTY, "Örnek Leipomo", NEW)
    with pytest.raises(LedgerRefusal, match="needs its name"):
        upsert_client(EMPTY, "ornek-leipomo", NOTHING)
    with pytest.raises(LedgerRefusal, match="no client status"):
        upsert_client(
            _one_client(),
            "ornek-leipomo",
            ClientChange(name=None, contact=None, status="hot", expecting=None, next_step=None, next_at=None),
        )


def test_an_outcome_is_dated_and_sourced_kept_once_and_never_of_an_unknown_client() -> None:
    ledger, added = note_outcome(_one_client(), "ornek-leipomo", "reply", "2026-11-20 08:15", "Mail from myynti@")
    assert added
    again, added_again = note_outcome(ledger, "ornek-leipomo", "reply", "2026-11-20 08:15", "Mail from myynti@")
    assert (again, added_again) == (ledger, False)
    with pytest.raises(LedgerRefusal, match="no outcome is guessed"):
        note_outcome(ledger, "ornek-leipomo", "reply", "2026-11-20", " ")
    with pytest.raises(LedgerRefusal, match="is no date"):
        note_outcome(ledger, "ornek-leipomo", "reply", "last week", "Mail")
    with pytest.raises(LedgerRefusal, match="no client has the id"):
        note_outcome(ledger, "someone-else", "reply", "2026-11-20", "Mail")


def test_the_ways_of_acting_are_compared_by_answers_that_came_after_the_send() -> None:
    ledger: Ledger = EMPTY
    for client in ("a-oy", "b-oy", "c-oy"):
        ledger = upsert_client(
            ledger,
            client,
            ClientChange(name=client, contact="", status=None, expecting=None, next_step=None, next_at=None),
        )
    for client, variant in (("a-oy", "problem"), ("b-oy", "problem"), ("c-oy", "short")):
        ledger = begin(ledger, client, "first-mail", variant, "2026-11-01 10:00")
        ledger = end(ledger, f"{client}/first-mail", "sent", "In Sent", "2026-11-01 10:01")
    ledger, _ = note_outcome(ledger, "a-oy", "reply", "2026-11-02 09:00", "Mail")
    # A reply dated before the send answered something else.
    ledger, _ = note_outcome(ledger, "c-oy", "reply", "2026-10-30 09:00", "Mail")
    ledger, _ = note_outcome(ledger, "a-oy", "won", "2026-11-10", "Signed offer")
    results = {result.variant: result for result in compare(ledger)}
    assert (results["problem"].reached, results["problem"].answered, results["problem"].won) == (2, 1, 1)
    assert (results["short"].reached, results["short"].answered) == (1, 0)
    assert "too few to tell" in summary(ledger, None)


def test_a_block_whose_markers_or_columns_were_broken_is_not_written_over() -> None:
    text: str = written(OWNERS_RECORD, _one_client())
    with pytest.raises(LedgerFormatError, match="markers"):
        parse(text.replace(END_MARK, ""))
    with pytest.raises(LedgerFormatError, match="columns"):
        parse(text.replace("| id | name |", "| id | firm |"))
