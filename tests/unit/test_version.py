"""The version Dot reports (`/api/health`, the User-Agent of its model calls) is the one it is released under: the
package's `__version__` and `pyproject.toml`'s are the same (item 348; the release workflow checks the tag against
both)."""

import tomllib
from pathlib import Path

import dotagent


def test_the_package_and_pyproject_say_the_same_version() -> None:
    pyproject: Path = Path(__file__).resolve().parents[2] / "pyproject.toml"
    with pyproject.open("rb") as stream:
        project: dict[str, object] = tomllib.load(stream)["project"]
    assert project["version"] == dotagent.__version__
