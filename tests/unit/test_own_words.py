"""The repository names no other product (item 369): Dot's code, tests, docs and roadmap describe Dot in its own words.
Such a name stays only in a licence notice the law asks to keep with code derived from another project
(``static/js/appearance/LICENSE``), and what ``integrations/`` vendors keeps the words of its upstream."""

import re
import subprocess
from pathlib import Path

_ROOT: Path = Path(__file__).resolve().parents[2]
# The names, also split over a line break or joined with a dash or an underscore.
_NAMES: re.Pattern[str] = re.compile(r"t3[\s_-]*(?:code|chat|tools)\b|pingdotgg", re.IGNORECASE)


# This file names what it looks for.
_ITSELF: str = "tests/unit/test_own_words.py"


def _checked(path: str) -> bool:
    """Whether the file at ``path`` (from the repository's root) is Dot's own: not a licence notice, not vendored."""
    return not (path.rsplit("/", 1)[-1].startswith("LICENSE") or path.startswith("integrations/") or path == _ITSELF)


def test_no_file_of_the_repository_names_another_product() -> None:
    listed: str = subprocess.run(
        ["git", "ls-files", "-z"], cwd=_ROOT, capture_output=True, text=True, check=True
    ).stdout
    found: list[str] = []
    for path in filter(_checked, filter(None, listed.split("\0"))):
        try:
            text: str = (_ROOT / path).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue  # an image or a font: no words to read
        found.extend(f"{path}:{text.count(chr(10), 0, match.start()) + 1}" for match in _NAMES.finditer(text))
    assert found == [], found
