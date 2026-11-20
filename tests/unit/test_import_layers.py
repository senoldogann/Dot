"""Pure-logic layers import when the macOS frameworks cannot.

``llm``, ``policy``, ``store``, ``memory``, ``config`` and ``tools.base`` are what a check on Linux can load.
They must not import AppKit, Quartz or ApplicationServices while they are imported. Those frameworks belong to
the modules that drive the desktop and the browser window, loaded when that code runs. The gate's Linux job runs the
unit tests whole since item 1270, which this test keeps checked on a Mac too.
"""

import os
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2] / "src" / "dotagent"
_PACKAGES = ("llm", "policy", "store", "memory")
_MODULES = (
    "dotagent.config",
    "dotagent.tools.base",
    "dotagent.tools.context",
    "dotagent.tools.shell",
    "dotagent.claude_code.env",
    "dotagent.claude_code.ask",
    "dotagent.repair.claude",
)
_FRAMEWORKS = ("AppKit", "Quartz", "ApplicationServices", "CoreFoundation", "Foundation", "objc")

_PROBE = """
import importlib
import os
import sys
import traceback

_FRAMEWORKS = frozenset({frameworks!r})


class _RefuseFrameworks:
    def find_spec(self, fullname, path, target=None):
        root = fullname.partition(".")[0]
        if root in _FRAMEWORKS:
            raise ModuleNotFoundError(f"No module named {{root!r}}")
        return None


sys.meta_path.insert(0, _RefuseFrameworks())
for name in {modules!r}:
    try:
        importlib.import_module(name)
    except Exception:
        print(name, file=sys.stderr)
        traceback.print_exc()
        raise SystemExit(1)
"""


def _pure_modules() -> tuple[str, ...]:
    """Every module of the pure packages, then the modules the model router reaches on its way to a tool."""
    found: list[str] = []
    for package in _PACKAGES:
        for path in sorted((_ROOT / package).rglob("*.py")):
            relative = path.relative_to(_ROOT).with_suffix("")
            parts = relative.parts[:-1] if relative.name == "__init__" else relative.parts
            if parts:
                found.append("dotagent." + ".".join(parts))
    return tuple(dict.fromkeys((*found, *_MODULES)))


def test_pure_layers_import_when_macos_frameworks_cannot(tmp_path: Path) -> None:
    """A fresh interpreter that cannot import the frameworks still imports the pure layers."""
    probe = _PROBE.format(frameworks=_FRAMEWORKS, modules=_pure_modules())
    environment = {
        **os.environ,
        "DOT_HOME": str(tmp_path / "home"),
        "DOT_LOG_DIR": str(tmp_path / "logs"),
        "DOT_CHROME_DIR": str(tmp_path / "chrome"),
    }
    done = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
