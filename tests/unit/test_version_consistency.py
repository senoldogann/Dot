"""The files a release is built from state one version.

``scripts/check_versions.py`` reads a checkout and fails when ``pyproject.toml`` and
``desktop/src-tauri/tauri.conf.json`` disagree, and when ``desktop/src-tauri/Cargo.toml`` or
``desktop/package.json`` carries a different version. The root ``package.json`` is the private package of
the interface checks, not a release. The gate's checks job runs the script.
"""

import json
import subprocess
import sys
from pathlib import Path

import yaml

_ROOT: Path = Path(__file__).resolve().parents[2]
_SCRIPT: Path = _ROOT / "scripts" / "check_versions.py"


def _write(
    root: Path,
    *,
    pyproject: str = "0.1.0",
    tauri: str = "0.1.0",
    init: str = "0.1.0",
    cargo: str | None = "0.1.0",
    package: str | None = "0.1.0",
    interface: str | None = None,
) -> None:
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "dotagent-macos"\nversion = "{pyproject}"\n', encoding="utf-8"
    )
    desktop = root / "desktop"
    tauri_dir = desktop / "src-tauri"
    tauri_dir.mkdir(parents=True, exist_ok=True)
    (tauri_dir / "tauri.conf.json").write_text(json.dumps({"productName": "Dot", "version": tauri}), encoding="utf-8")
    package_dir = root / "src" / "dotagent"
    package_dir.mkdir(parents=True, exist_ok=True)
    (package_dir / "__init__.py").write_text(f'__version__ = "{init}"\n', encoding="utf-8")
    if cargo is not None:
        (tauri_dir / "Cargo.toml").write_text(
            f'[package]\nname = "dot-desktop"\nversion = "{cargo}"\n', encoding="utf-8"
        )
    if package is not None:
        (desktop / "package.json").write_text(json.dumps({"name": "dot-desktop", "version": package}), encoding="utf-8")
    if interface is not None:
        (root / "package.json").write_text(
            json.dumps({"name": "dot-interface-checks", "private": True, "version": interface}), encoding="utf-8"
        )


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), str(root)],
        check=False,
        capture_output=True,
        text=True,
    )


def test_a_fixture_whose_versions_disagree_fails(tmp_path: Path) -> None:
    _write(tmp_path, pyproject="0.2.0", tauri="0.1.0", init="0.1.0", cargo="0.3.0", package="0.1.0")
    result = _run(tmp_path)
    assert result.returncode != 0
    assert "pyproject.toml" in result.stderr
    assert "tauri.conf.json" in result.stderr
    assert "Cargo.toml" in result.stderr
    assert "0.2.0" in result.stderr
    assert "0.1.0" in result.stderr
    assert "0.3.0" in result.stderr


def test_versions_that_agree_pass_and_the_interface_package_is_not_a_release(tmp_path: Path) -> None:
    _write(tmp_path, interface="9.9.9")
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0.1.0"
    assert "9.9.9" not in result.stdout and "9.9.9" not in result.stderr


def test_a_carried_package_json_or_cargo_version_that_differs_fails(tmp_path: Path) -> None:
    _write(tmp_path, package="0.9.0")
    package = _run(tmp_path)
    assert package.returncode != 0
    assert "package.json" in package.stderr and "0.9.0" in package.stderr

    _write(tmp_path, package="0.1.0", cargo="0.8.0")
    cargo = _run(tmp_path)
    assert cargo.returncode != 0
    assert "Cargo.toml" in cargo.stderr and "0.8.0" in cargo.stderr


def test_a_cargo_toml_or_package_json_that_carries_no_version_is_left_out(tmp_path: Path) -> None:
    _write(tmp_path, cargo=None, package=None)
    (tmp_path / "desktop" / "src-tauri" / "Cargo.toml").write_text(
        '[package]\nname = "dot-desktop"\nedition = "2021"\n', encoding="utf-8"
    )
    (tmp_path / "desktop" / "package.json").write_text(
        json.dumps({"name": "dot-desktop", "private": True}), encoding="utf-8"
    )
    result = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0.1.0"


def test_a_missing_release_version_fails(tmp_path: Path) -> None:
    _write(tmp_path)
    (tmp_path / "desktop" / "src-tauri" / "tauri.conf.json").unlink()
    result = _run(tmp_path)
    assert result.returncode != 0
    assert "tauri.conf.json" in result.stderr


def test_the_repository_has_one_version() -> None:
    result = _run(_ROOT)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "0.1.0"


def test_the_checks_job_runs_the_version_script() -> None:
    loaded: object = yaml.safe_load((_ROOT / ".github" / "workflows" / "gate.yml").read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    steps = loaded["jobs"]["checks"]["steps"]
    assert any("scripts/check_versions.py" in str(step.get("run", "")) for step in steps)
