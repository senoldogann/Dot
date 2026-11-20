"""Fail when the files a release is built from do not state one version.

The release version is ``pyproject.toml``'s ``[project].version``,
``desktop/src-tauri/tauri.conf.json``'s ``version`` and ``src/dotagent/__init__.py``'s
``__version__``. ``desktop/src-tauri/Cargo.toml``'s ``[package].version`` and
``desktop/package.json``'s ``version`` join them when those files carry one. The root
``package.json`` is the private package of the interface checks and is not read.

Prints the version when they agree. Otherwise prints one line per file to stderr and exits 1.

Usage: python scripts/check_versions.py [ROOT]
"""

import json
import re
import sys
import tomllib
from pathlib import Path

_INIT: re.Pattern[str] = re.compile(r"""^__version__\s*=\s*["']([^"']+)["']\s*$""", re.MULTILINE)


class _Absent:
    """A Cargo.toml or package.json that does not carry a version."""


_ABSENT = _Absent()


def _read_toml(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as stream:
            loaded: object = tomllib.load(stream)
    except FileNotFoundError:
        return {}
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"{path}: {exc}") from exc
    if not isinstance(loaded, dict):
        raise ValueError(f"{path}: expected a table")
    return loaded


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path}: {exc}") from exc
    except OSError as exc:
        raise ValueError(f"{path}: {exc}") from exc


def _stated(value: object) -> str | None:
    """A non-empty string version, or ``None`` when the file carries something else."""
    if isinstance(value, str) and value != "":
        return value
    return None


def _project_version(root: Path) -> str | None:
    project: object = _read_toml(root / "pyproject.toml").get("project")
    if not isinstance(project, dict):
        return None
    return _stated(project.get("version"))


def _tauri_version(root: Path) -> str | None:
    loaded: object = _read_json(root / "desktop" / "src-tauri" / "tauri.conf.json")
    if not isinstance(loaded, dict):
        return None
    return _stated(loaded.get("version"))


def _init_version(root: Path) -> str | None:
    path: Path = root / "src" / "dotagent" / "__init__.py"
    try:
        text: str = path.read_text(encoding="utf-8")
    except OSError:
        return None
    found: re.Match[str] | None = _INIT.search(text)
    if found is None:
        return None
    return found.group(1)


def _cargo_version(root: Path) -> str | _Absent | None:
    """The crate version, ``_ABSENT`` when ``Cargo.toml`` carries none, or ``None`` when it is not text."""
    path: Path = root / "desktop" / "src-tauri" / "Cargo.toml"
    if not path.is_file():
        return _ABSENT
    package: object = _read_toml(path).get("package")
    if not isinstance(package, dict) or "version" not in package:
        return _ABSENT
    return _stated(package.get("version"))


def _package_version(root: Path) -> str | _Absent | None:
    """``desktop/package.json``'s version, ``_ABSENT`` when it carries none, or ``None`` when it is not text."""
    path: Path = root / "desktop" / "package.json"
    if not path.is_file():
        return _ABSENT
    loaded: object = _read_json(path)
    if not isinstance(loaded, dict) or "version" not in loaded:
        return _ABSENT
    return _stated(loaded.get("version"))


def versions(root: Path) -> list[tuple[str, str | None]]:
    """Each release file that must agree, with ``None`` where it does not state a version."""
    rows: list[tuple[str, str | None]] = [
        ("pyproject.toml", _project_version(root)),
        ("desktop/src-tauri/tauri.conf.json", _tauri_version(root)),
        ("src/dotagent/__init__.py", _init_version(root)),
    ]
    cargo: str | _Absent | None = _cargo_version(root)
    if not isinstance(cargo, _Absent):
        rows.append(("desktop/src-tauri/Cargo.toml", cargo))
    package: str | _Absent | None = _package_version(root)
    if not isinstance(package, _Absent):
        rows.append(("desktop/package.json", package))
    return rows


def main(argv: list[str]) -> int:
    """Exit 0 and print the version when ``argv``'s checkout agrees; 1 otherwise."""
    if len(argv) > 2:
        print("usage: check_versions.py [ROOT]", file=sys.stderr)
        return 2
    root: Path = Path(argv[1]) if len(argv) == 2 else Path(__file__).resolve().parents[1]
    try:
        rows: list[tuple[str, str | None]] = versions(root)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    stated: set[str] = {version for _, version in rows if version is not None}
    if any(version is None for _, version in rows) or len(stated) != 1:
        print("versions disagree:", file=sys.stderr)
        for label, version in rows:
            print(f"  {label}: {version if version is not None else 'missing'}", file=sys.stderr)
        return 1
    print(stated.pop())
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
