"""The gate's interface install does not run package scripts."""

from pathlib import Path

_ACTION: Path = Path(__file__).resolve().parents[2] / ".github" / "actions" / "prepare" / "action.yml"


def test_the_interface_install_ignores_package_scripts() -> None:
    """Biome and tsc need no install script, and a postinstall must not run on the runner."""
    text: str = _ACTION.read_text(encoding="utf-8")
    assert "npm ci --ignore-scripts" in text
    assert "run: npm ci\n" not in text
