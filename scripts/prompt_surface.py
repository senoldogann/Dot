"""Write the prompt surface Dot sends a model into ``tests/fixtures/prompt_surface/`` (item 1858): the system prompt of
a few kinds of run and every built-in tool's schema (:mod:`dotagent.evals.prompt_surface`). Run it after a change that
alters what the model is told, and commit the files with the change: ``tests/unit/test_prompt_surface.py`` fails until
they match, and the review reads the change as their diff.

With ``--check`` it writes nothing: it lists the files that are stale and exits 1 (item 1971: the pre-push hook runs it,
since that test runs on macOS only and a pull request's Linux jobs never see a stale fixture).

Usage: uv run python scripts/prompt_surface.py [--check]
"""

import argparse
import sys
from pathlib import Path

from dotagent.evals.prompt_surface import SURFACE_DIR, render_surface, stale_files, write_surface

FIX: str = "uv run python scripts/prompt_surface.py"


def main() -> int:
    parser: argparse.ArgumentParser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 when a fixture is stale")
    check: bool = parser.parse_args().check
    folder: Path = Path(__file__).resolve().parent.parent / SURFACE_DIR
    rendered: dict[str, str] = render_surface()
    changed: list[str] = stale_files(folder, rendered)
    if check:
        if not changed:
            print("prompt surface: up to date")
            return 0
        print(
            f"prompt surface: stale ({', '.join(changed)}). Run `{FIX}` and commit {SURFACE_DIR} with the change.",
            file=sys.stderr,
        )
        return 1
    write_surface(folder, rendered)
    print(f"changed: {', '.join(changed)}" if changed else "unchanged")
    print(rendered["summary.txt"], end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
