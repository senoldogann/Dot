"""Write the prompt surface Dot sends a model into ``tests/fixtures/prompt_surface/`` (item 1858): the system prompt of
a few kinds of run and every built-in tool's schema (:mod:`dotagent.evals.prompt_surface`). Run it after a change that
alters what the model is told, and commit the files with the change: ``tests/unit/test_prompt_surface.py`` fails until
they match, and the review reads the change as their diff.

Usage: uv run python scripts/prompt_surface.py
"""

from pathlib import Path

from dotagent.evals.prompt_surface import SURFACE_DIR, render_surface, stale_files, write_surface


def main() -> None:
    folder: Path = Path(__file__).resolve().parent.parent / SURFACE_DIR
    rendered: dict[str, str] = render_surface()
    changed: list[str] = stale_files(folder, rendered)
    write_surface(folder, rendered)
    print(f"changed: {', '.join(changed)}" if changed else "unchanged")
    print(rendered["summary.txt"], end="")


if __name__ == "__main__":
    main()
