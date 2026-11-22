"""What the code imports is what pyproject.toml declares (item 1316).

A module the code imports directly but only another package pulls in works until that package drops it, and `uv sync`
on a fresh Mac says nothing of it. This walks every file under ``src/dotagent`` with ``ast``, takes the third-party
modules it imports (at the top of a file or inside a function), and fails for one that is no standard module, no part
of ``dotagent`` and no distribution pyproject.toml names (``dependencies``, an optional group or a dependency group),
unless the allowlist below says why it is left out.
"""

import ast
import re
import sys
import tomllib
from importlib.metadata import packages_distributions
from pathlib import Path

ROOT: Path = Path(__file__).resolve().parents[2]
SOURCE: Path = ROOT / "src" / "dotagent"

# Modules whose distribution is not named like them, so that the check needs no installed package to know them (PyObjC
# exists on macOS alone, so the installed metadata cannot say where ``objc`` comes from on Linux).
_DISTRIBUTION_OF: dict[str, str] = {
    "yaml": "pyyaml",
    "docx": "python-docx",
    "pptx": "python-pptx",
    "PIL": "pillow",
    "mcp_types": "mcp-types",
    "objc": "pyobjc-core",
    "PyObjCTools": "pyobjc-core",
    "AppKit": "pyobjc-framework-cocoa",
    "Foundation": "pyobjc-framework-cocoa",
    "CoreFoundation": "pyobjc-framework-cocoa",
    "Quartz": "pyobjc-framework-quartz",
    "ApplicationServices": "pyobjc-framework-applicationservices",
    "ScreenCaptureKit": "pyobjc-framework-screencapturekit",
    "VideoToolbox": "pyobjc-framework-videotoolbox",
    "Vision": "pyobjc-framework-vision",
    "CoreMedia": "pyobjc-framework-coremedia",
}

# Imports that are left undeclared on purpose, each with the reason. Keep it short: a module that is imported for real
# belongs in pyproject.toml.
ALLOWED_UNDECLARED: dict[str, str] = {}


def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def declared_distributions() -> set[str]:
    """The distributions pyproject.toml names, in any list: dependencies, optional groups and dependency groups."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requirements: list[str] = list(data["project"].get("dependencies", []))
    for group in data["project"].get("optional-dependencies", {}).values():
        requirements.extend(group)
    for group in data.get("dependency-groups", {}).values():
        requirements.extend(entry for entry in group if isinstance(entry, str))
    names: set[str] = set()
    for requirement in requirements:
        match = re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*", requirement)
        assert match is not None, f"pyproject.toml has a requirement that names no package: {requirement!r}"
        names.add(_normalized(match.group(0)))
    return names


def imported_modules(source: str) -> set[str]:
    """The top-level names of the modules a file imports by an absolute import, anywhere in the file."""
    found: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module is not None:
            found.add(node.module.split(".")[0])
    return found


def undeclared_imports(
    files: list[Path], base: Path, declared: set[str], installed: dict[str, list[str]]
) -> dict[str, list[str]]:
    """Each third-party module the files import that no declared distribution provides, with the files importing it."""
    found: dict[str, list[str]] = {}
    for path in files:
        for module in imported_modules(path.read_text(encoding="utf-8")):
            if module in sys.stdlib_module_names or module == "dotagent" or module in ALLOWED_UNDECLARED:
                continue
            candidates: list[str] = [_DISTRIBUTION_OF.get(module, module), *installed.get(module, [])]
            if not any(_normalized(candidate) in declared for candidate in candidates):
                found.setdefault(module, []).append(str(path.relative_to(base)))
    return found


def test_every_module_the_code_imports_is_declared() -> None:
    files: list[Path] = sorted(SOURCE.rglob("*.py"))
    assert len(files) > 100, f"the sources were not found under {SOURCE}"
    missing = undeclared_imports(files, ROOT, declared_distributions(), packages_distributions())
    assert missing == {}, (
        "imported by the code and declared nowhere in pyproject.toml (add the package, or allow it with a reason): "
        + "; ".join(f"{module} in {', '.join(sorted(set(where))[:3])}" for module, where in sorted(missing.items()))
    )


def test_an_import_that_nothing_declares_is_found(tmp_path: Path) -> None:
    source: Path = tmp_path / "a.py"
    source.write_text(
        "import os\nimport yaml\nfrom starlette.requests import Request\ndef f():\n    import secret_pkg\n"
    )
    found = undeclared_imports([source], tmp_path, {"pyyaml"}, {})
    assert sorted(found) == ["secret_pkg", "starlette"]


def test_an_import_a_declared_distribution_provides_is_not_found(tmp_path: Path) -> None:
    source: Path = tmp_path / "a.py"
    source.write_text("import objc\nfrom mcp_types import X\nimport httpx2\n")
    assert (
        undeclared_imports([source], tmp_path, {"pyobjc-core", "mcp-types", "httpx2"}, {"mcp_types": ["mcp-types"]})
        == {}
    )


def test_the_allowlist_gives_a_reason_for_each_entry() -> None:
    assert all(reason.strip() != "" for reason in ALLOWED_UNDECLARED.values())
