"""Routing should require a recognizable request and leave ambiguous goals alone."""

import pytest

from dotagent.agent.task_kind import task_kind
from dotagent.config import TaskKind


@pytest.mark.parametrize(
    ("goal", "kind"),
    [
        ("Implement a regression test for the queue.", "code"),
        ("Bu repodaki hatayı düzelt.", "code"),
        ("Draft an email for the release announcement.", "writing"),
        ("Müşteriye kısa bir mesaj yaz.", "writing"),
        ("Investigate why the sync queue keeps growing.", "deep"),
        ("Kuyruğun kök nedenini bul.", "deep"),
        ("Look up the date of the next meeting.", "quick"),
        ("Bu hafta kaç tane görüşme var?", "quick"),
        ("Look up the date, then draft a report.", None),
        ("Tell me about this project.", None),
    ],
)
def test_opening_request_selects_only_a_clear_kind(goal: str, kind: TaskKind | None) -> None:
    assert task_kind(goal) == kind
