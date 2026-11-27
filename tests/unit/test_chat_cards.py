"""The conversation accepts bounded card data and rejects ambiguous chart and action payloads."""

import math
from pathlib import Path

import pytest
from PIL import Image, UnidentifiedImageError
from pydantic import ValidationError

from dotagent.core.errors import ToolInputError
from dotagent.core.types import JsonObject
from dotagent.llm.types import ImageData
from dotagent.tools.cards import Chart, ShowCardsArgs, WorkspaceImage, _image_data


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


def _workspace_with_pngs(base: Path) -> Path:
    """A workspace holding a real PNG at ``dot.png``, ``crawl/page.png`` and ``mcp-results/answer.png``, and one beside
    it in ``outside/``: every refusal below is of a real raster, so only the path rules can refuse it."""
    workspace: Path = base / "ws"
    for folder in (workspace, workspace / "crawl", workspace / "mcp-results", base / "outside"):
        folder.mkdir(parents=True)
    for target in (
        workspace / "dot.png",
        workspace / "crawl" / "page.png",
        workspace / "mcp-results" / "answer.png",
        base / "outside" / "dot.png",
    ):
        Image.new("RGB", (2, 2)).save(target)
    return workspace


@pytest.mark.macos
def test_a_workspace_png_is_read_with_its_media_type(tmp_path: Path) -> None:
    workspace: Path = _workspace_with_pngs(tmp_path)
    data: ImageData = _image_data(WorkspaceImage(path="dot.png", alt="A dot"), workspace, (), ())
    assert data.media_type == "image/png"


@pytest.mark.macos
@pytest.mark.parametrize("where", ["absolute", "outside", "crawl", "mcp-results"])
def test_a_raster_outside_the_runs_own_files_is_refused_by_its_path(tmp_path: Path, where: str) -> None:
    workspace: Path = _workspace_with_pngs(tmp_path)
    path: str = {
        "absolute": str(workspace / "dot.png"),
        "outside": "../outside/dot.png",
        "crawl": "crawl/page.png",
        "mcp-results": "mcp-results/answer.png",
    }[where]
    with pytest.raises(ToolInputError, match="raster files you made in the workspace"):
        _image_data(WorkspaceImage(path=path, alt="A dot"), workspace, (), ())


@pytest.mark.macos
def test_a_link_out_of_the_workspace_and_a_svg_are_refused(tmp_path: Path) -> None:
    workspace: Path = _workspace_with_pngs(tmp_path)
    (workspace / "link.png").symlink_to(tmp_path / "outside" / "dot.png")
    (workspace / "mark.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'/>", encoding="utf-8")
    with pytest.raises(ToolInputError, match="raster files you made in the workspace"):
        _image_data(WorkspaceImage(path="link.png", alt="A link"), workspace, (), ())
    with pytest.raises(UnidentifiedImageError):
        _image_data(WorkspaceImage(path="mark.svg", alt="A mark"), workspace, (), ())
