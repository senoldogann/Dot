"""The version Dot reports (`/api/health`, the User-Agent of its model calls) is the one it is released under: the
package's `__version__` and `pyproject.toml`'s are the same (item 348; the release workflow checks the tag against
both), and so is the desktop app's (item 432; `desktop/release.sh` builds it under that version)."""

import json
import tomllib
from pathlib import Path

import dotagent

_ROOT: Path = Path(__file__).resolve().parents[2]


def test_the_package_and_pyproject_say_the_same_version() -> None:
    with (_ROOT / "pyproject.toml").open("rb") as stream:
        project: dict[str, object] = tomllib.load(stream)["project"]
    assert project["version"] == dotagent.__version__


def test_the_desktop_app_says_the_version_of_the_package() -> None:
    tauri: dict[str, object] = json.loads((_ROOT / "desktop/src-tauri/tauri.conf.json").read_text())
    npm: dict[str, object] = json.loads((_ROOT / "desktop/package.json").read_text())
    with (_ROOT / "desktop/src-tauri/Cargo.toml").open("rb") as stream:
        cargo: dict[str, object] = tomllib.load(stream)["package"]
    assert [tauri["version"], npm["version"], cargo["version"]] == [dotagent.__version__] * 3
