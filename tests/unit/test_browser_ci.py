"""scripts/browser_ci.py: which files of a pull request run the browser parts of the gate, and that those parts still
cover every file they covered before the split. The import walk is what fails the day a module the parts are told to
ignore becomes one the daemon imports."""

import ast
import importlib.util
from pathlib import Path
from types import ModuleType

import yaml

_ROOT: Path = Path(__file__).resolve().parents[2]
_SCRIPT: Path = _ROOT / "scripts" / "browser_ci.py"
_SMOKE: Path = _ROOT / "tests" / "smoke"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("browser_ci", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


browser_ci = _load()


def test_a_page_a_test_and_a_lock_run_the_browser_parts_and_a_unit_test_does_not() -> None:
    assert browser_ci.affects_browser_tests("src/dotagent/server/static/js/views/chat.js")
    assert browser_ci.affects_browser_tests("src/dotagent/server/app.py")
    assert browser_ci.affects_browser_tests("src/dotagent/browser/session.py")
    assert browser_ci.affects_browser_tests("src/dotagent/agent/loop.py")
    assert browser_ci.affects_browser_tests("src/dotagent/runtime.py")
    assert browser_ci.affects_browser_tests("tests/smoke/test_phone_all_pages.py")
    assert browser_ci.affects_browser_tests("tests/smoke/test_browser.py")
    assert browser_ci.affects_browser_tests("tests/smoke/test_browser_attach.py")
    assert browser_ci.affects_browser_tests("tests/smoke/test_secret_fields.py")
    assert browser_ci.affects_browser_tests("tests/smoke/form_dialogs.py")
    assert browser_ci.affects_browser_tests("tests/conftest.py")
    assert browser_ci.affects_browser_tests("tests/smoke/conftest.py")
    assert browser_ci.affects_browser_tests("pyproject.toml")
    assert browser_ci.affects_browser_tests("uv.lock")
    assert browser_ci.affects_browser_tests(".github/workflows/gate.yml")
    assert browser_ci.affects_browser_tests(".github/actions/prepare/action.yml")
    assert browser_ci.affects_browser_tests("scripts/browser_ci.py")
    assert not browser_ci.affects_browser_tests("tests/unit/test_loop.py")
    assert not browser_ci.affects_browser_tests("tests/smoke/test_scheduler.py")
    assert not browser_ci.affects_browser_tests("src/dotagent/cli_run.py")
    assert not browser_ci.affects_browser_tests("src/dotagent/cli.py")
    assert not browser_ci.affects_browser_tests("src/dotagent/watchdog.py")
    assert not browser_ci.affects_browser_tests("src/dotagent/evals/scoring.py")
    assert not browser_ci.affects_browser_tests("desktop/src-tauri/src/main.rs")
    assert not browser_ci.affects_browser_tests("docs/development.md")
    assert not browser_ci.affects_browser_tests(".github/workflows/zizmor.yml")
    assert not browser_ci.affects_browser_tests("scripts/merge_pr.py")
    assert browser_ci.runs_browser_tests(["tests/unit/test_loop.py", "src/dotagent/server/app.py"])
    assert not browser_ci.runs_browser_tests(["tests/unit/test_loop.py", "src/dotagent/cli_run.py", "roadmap/1.md"])
    assert not browser_ci.runs_browser_tests([])


def _modules() -> dict[str, Path]:
    """Every module of the package, by the name it is imported under."""
    found: dict[str, Path] = {}
    for path in (_ROOT / "src" / "dotagent").rglob("*.py"):
        relative = path.relative_to(_ROOT / "src").with_suffix("")
        parts = list(relative.parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        found[".".join(parts)] = path
    return found


def _imported(path: Path, module: str) -> list[str]:
    """The modules ``path`` imports, relative imports resolved from ``module``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = module if path.name == "__init__.py" else ".".join(module.split(".")[:-1])
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module or ""
            else:
                base_parts = package.split(".") if package else []
                up = node.level - 1
                if up:
                    base_parts = base_parts[:-up] if up <= len(base_parts) else []
                if node.module:
                    base_parts.extend(node.module.split("."))
                base = ".".join(base_parts)
            if not node.names or any(alias.name == "*" for alias in node.names):
                found.append(base)
            else:
                found.extend(f"{base}.{alias.name}" if base else alias.name for alias in node.names)
    return found


def _resolve(name: str, modules: dict[str, Path]) -> str | None:
    parts = name.split(".")
    while parts:
        candidate = ".".join(parts)
        if candidate in modules:
            return candidate
        parts.pop()
    return None


def _daemon_imports() -> set[str]:
    """The modules the daemon and the browser parts' tests import, as paths from the repository root.

    Importing a module runs its package's ``__init__`` too, so those are walked with it."""
    modules = _modules()
    seeds: list[str] = ["dotagent.runtime", "dotagent.server.app", "dotagent.server.daemon"]
    for path in list(_SMOKE.glob("test_browser*.py")) + list(_SMOKE.glob("test_phone*.py")):
        seeds.extend(name for name in _imported(path, "") if name.startswith("dotagent"))
    for name in ("test_secret_fields.py", "test_page_guards.py", "test_face.py"):
        seeds.extend(name for name in _imported(_SMOKE / name, "") if name.startswith("dotagent"))
    seen: set[str] = set()
    stack: list[str] = list(seeds)
    while stack:
        resolved = _resolve(stack.pop(), modules)
        if resolved is None or resolved in seen:
            continue
        seen.add(resolved)
        stack.extend(name for name in _imported(modules[resolved], resolved) if name.startswith("dotagent"))
        parts = resolved.split(".")
        for index in range(1, len(parts)):
            stack.append(".".join(parts[:index]))
    paths: set[str] = set()
    for name in seen:
        relative = modules[name].relative_to(_ROOT).as_posix()
        paths.add(relative)
    return paths


def test_nothing_the_daemon_imports_is_left_out_of_the_browser_parts() -> None:
    """A module the running daemon imports can break a browser part at startup, so a change to it runs the parts."""
    left_out = sorted(path for path in _daemon_imports() if not browser_ci.affects_browser_tests(path))
    assert left_out == []


def _part_files() -> dict[str, set[str]]:
    """The test files each browser part runs, globs expanded and whole-file deselects removed."""
    loaded = yaml.safe_load((_ROOT / ".github" / "workflows" / "gate.yml").read_text(encoding="utf-8"))
    parts = loaded["jobs"]["browser"]["strategy"]["matrix"]["part"]
    found: dict[str, set[str]] = {}
    for part in parts:
        tokens: list[str] = str(part["tests"]).split()
        selected: list[str] = []
        dropped: set[str] = set()
        index = 0
        while index < len(tokens):
            if tokens[index] == "--deselect":
                node = tokens[index + 1]
                if "::" not in node:
                    dropped.add(node)
                index += 2
            else:
                selected.append(tokens[index])
                index += 1
        files: set[str] = set()
        for pattern in selected:
            matches = [path for path in _ROOT.glob(pattern) if path.is_file()]
            assert matches, pattern
            files.update(path.relative_to(_ROOT).as_posix() for path in matches)
        files -= dropped
        found[str(part["name"])] = files
    return found


def test_the_three_parts_cover_the_browser_files_once() -> None:
    parts = _part_files()
    assert set(parts) == {"pages", "phone 1", "phone 2"}
    covered: set[str] = set()
    for files in parts.values():
        assert covered.isdisjoint(files)
        covered |= files
    expected = {
        path.relative_to(_ROOT).as_posix()
        for path in (
            list(_SMOKE.glob("test_browser_*.py"))
            + list(_SMOKE.glob("test_phone_*.py"))
            + [_SMOKE / "test_secret_fields.py", _SMOKE / "test_page_guards.py", _SMOKE / "test_face.py"]
        )
    }
    assert covered == expected
    phone_2 = str(
        yaml.safe_load((_ROOT / ".github" / "workflows" / "gate.yml").read_text(encoding="utf-8"))["jobs"]["browser"][
            "strategy"
        ]["matrix"]["part"][2]["tests"]
    )
    assert "test_a_phone_back_from_the_background_keeps_its_chat_still_while_the_network_wakes" in phone_2
