"""The interface map names every view file and the smoke tests that anchor the pages (item 803)."""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VIEWS = ROOT / "src" / "dotagent" / "server" / "static" / "js" / "views"
DOC = ROOT / "docs" / "dot-ui.md"
SMOKE = (
    ROOT / "tests" / "smoke" / "test_harness_page.py",
    ROOT / "tests" / "smoke" / "test_memory_page.py",
    ROOT / "tests" / "smoke" / "test_rules.py",
)
ROW = re.compile(r"^\| .+? \| `([A-Za-z]+\.js)` \| .+ \|$")


def _mapped() -> set[str]:
    return {match[1] for line in DOC.read_text(encoding="utf-8").splitlines() if (match := ROW.match(line)) is not None}


def test_every_view_file_has_one_row_in_the_ui_map() -> None:
    assert _mapped() == {path.name for path in VIEWS.glob("*.js")}


def test_the_map_names_three_smoke_tests_that_exist_and_test_something() -> None:
    assert len(SMOKE) >= 3
    for path in SMOKE:
        assert path.is_file(), f"{path} is named by the map but does not exist"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test_")
            for node in ast.walk(tree)
        ), f"{path} has no test function"
