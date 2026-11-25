"""scripts/python_ci.py: which files of a pull request run the gate's macOS jobs. Only files nothing of those jobs reads
skip them; a file the script does not know runs them, and so does an empty list."""

import importlib.util
from pathlib import Path
from types import ModuleType

_ROOT: Path = Path(__file__).resolve().parents[2]
_SCRIPT: Path = _ROOT / "scripts" / "python_ci.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("python_ci", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module: ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


python_ci = _load()


def test_the_package_its_tests_its_locks_and_the_gate_run_the_macos_jobs() -> None:
    for path in (
        "src/dotagent/agent/loop.py",
        "src/dotagent/server/static/js/views/chat.js",
        "src/dotagent/skills/builtin/deep-os/SKILL.md",
        "tests/unit/test_shell_policy.py",
        "tests/smoke/test_web.py",
        "tests/conftest.py",
        "pyproject.toml",
        "uv.lock",
        "package.json",
        "scripts/browser_ci.py",
        "scripts/python_ci.py",
        ".github/workflows/gate.yml",
        ".github/actions/prepare/action.yml",
        "desktop/ui/installer.html",
        "desktop/src-tauri/src/main.rs",
        "docs/freedom.md",
        "evals/cases.json",
        "something/new.txt",
    ):
        assert python_ci.affects_python_gate(path), path


def test_the_iphone_app_the_item_records_and_prose_do_not() -> None:
    for path in (
        "ios/Next/App/ChatScreen.swift",
        "ios/project.yml",
        "roadmap/1850.md",
        "roadmap/INDEX.md",
        "changes/1850.md",
        "docs/phone.md",
        "README.md",
        "CHANGELOG.md",
        ".github/workflows/ios.yml",
        ".github/workflows/desktop.yml",
        ".github/ISSUE_TEMPLATE/bug.md",
        ".github/pull_request_template.md",
    ):
        assert not python_ci.affects_python_gate(path), path


def test_one_file_that_can_affect_them_runs_them_and_no_file_at_all_runs_them() -> None:
    assert not python_ci.runs_python_gate(["ios/Next/App/ChatScreen.swift", "roadmap/1850.md", "changes/1850.md"])
    assert python_ci.runs_python_gate(["ios/Next/App/ChatScreen.swift", "src/dotagent/runtime.py"])
    assert python_ci.runs_python_gate([])
