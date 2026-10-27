"""What the daemon loads when it starts (item 729): the libraries only some tools need are loaded by their first use.
python-docx, openpyxl and python-pptx (with numpy, which openpyxl loads) weighed about 60 MB, Vision and pywebpush
(with aiohttp) about 25 MB more, at every start of the daemon."""

import os
import subprocess
import sys
from pathlib import Path

LAZY: tuple[str, ...] = ("docx", "openpyxl", "pptx", "numpy", "Vision", "pywebpush", "aiohttp")


def test_the_daemon_module_loads_none_of_the_libraries_of_single_tools(tmp_path: Path) -> None:
    probe: str = (
        f"import sys, dotagent.server.daemon\nprint(','.join(name for name in {LAZY!r} if name in sys.modules))\n"
    )
    environment: dict[str, str] = {**os.environ, "DOT_HOME": str(tmp_path / "home"), "DOT_LOG_DIR": str(tmp_path)}
    done = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, env=environment, check=True, timeout=120
    )
    assert done.stdout.strip() == "", done.stdout
