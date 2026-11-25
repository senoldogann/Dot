"""The prompt surface is checked in (item 1858): the system prompts and the tool schemas Dot sends a model, rendered
with fixed machine facts, match ``tests/fixtures/prompt_surface/``. A change that alters what the model is told fails
here until ``uv run python scripts/prompt_surface.py`` writes the files again in the same pull request, so that the
review reads it as a diff, with the size of each part in ``summary.txt``."""

import difflib
from pathlib import Path

import pytest

from dotagent.evals.prompt_surface import SURFACE_DIR, render_surface, stale_files

_FOLDER: Path = Path(__file__).resolve().parent.parent.parent / SURFACE_DIR
_SHOWN_LINES: int = 60


def test_the_checked_in_prompt_surface_is_what_dot_sends() -> None:
    rendered: dict[str, str] = render_surface()
    stale: list[str] = stale_files(_FOLDER, rendered)
    if not stale:
        return
    shown: list[str] = []
    for name in stale:
        kept: Path = _FOLDER / name
        before: list[str] = kept.read_text(encoding="utf-8").splitlines() if kept.exists() else []
        after: list[str] = rendered.get(name, "").splitlines()
        shown.extend(difflib.unified_diff(before, after, f"checked in/{name}", f"rendered/{name}", lineterm="", n=1))
    pytest.fail(
        "The prompt surface changed: run `uv run python scripts/prompt_surface.py` and commit "
        f"{SURFACE_DIR} with the change. Stale: {', '.join(stale)}.\n" + "\n".join(shown[:_SHOWN_LINES])
    )


def test_the_surface_does_not_depend_on_who_renders_it_or_where(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    first: dict[str, str] = render_surface()
    monkeypatch.setenv("DOT_HOME", str(tmp_path / "elsewhere"))
    monkeypatch.setenv("HOME", str(tmp_path / "someone"))
    monkeypatch.setenv("USER", "someone")
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    assert render_surface() == first
