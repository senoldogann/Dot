"""The conversation accepts bounded card data and rejects ambiguous chart and action payloads."""

import math

import pytest
from pydantic import ValidationError

from dotagent.core.types import JsonObject
from dotagent.tools.cards import Chart, ShowCardsArgs


@pytest.mark.parametrize(
    ("card", "error"),
    [
        ({"kind": "html", "html": "<script></script>"}, "literal_error"),
        (
            {"kind": "post_draft", "platform": "X", "account": "@owner", "text": "Hi", "publish": True},
            "extra_forbidden",
        ),
        ({"kind": "table", "title": "Data", "columns": ["a", "b"], "rows": [["a"]]}, "one cell per column"),
        ({"kind": "choice", "prompt": "Pick", "options": ["North", " North "]}, "distinct"),
        (
            {
                "kind": "chart",
                "title": "Data",
                "chart_type": "pie",
                "x": ["A"],
                "series": [{"name": "N", "values": [0]}],
            },
            "positive total",
        ),
        (
            {
                "kind": "chart",
                "title": "Data",
                "chart_type": "scatter",
                "x": ["A"],
                "series": [{"name": "N", "values": [2]}],
            },
            "numeric x",
        ),
    ],
)
def test_invalid_cards_have_specific_errors(card: JsonObject, error: str) -> None:
    with pytest.raises(ValidationError, match=error):
        ShowCardsArgs.model_validate({"cards": [card]})


@pytest.mark.parametrize("value", [math.inf, math.nan, "2", True])
def test_chart_values_must_be_finite_numbers(value: float | str | bool) -> None:
    with pytest.raises(ValidationError):
        Chart.model_validate(
            {
                "kind": "chart",
                "title": "Data",
                "chart_type": "line",
                "x": ["A"],
                "series": [{"name": "N", "values": [value]}],
            }
        )


def test_valid_scatter_accepts_numeric_x_values() -> None:
    chart: Chart = Chart.model_validate(
        {
            "kind": "chart",
            "title": "Data",
            "chart_type": "scatter",
            "x": [-2, 0.5],
            "series": [{"name": "N", "values": [0, 1]}],
        }
    )
    assert chart.x == (-2, 0.5)
    assert chart.series[0].values == (0, 1)
