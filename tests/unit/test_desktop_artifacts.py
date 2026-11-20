"""The desktop artifact workflow builds a disk image and latest.json and publishes nothing.

It is started by hand. Signing and notarization are skipped when their secrets are absent, and the
files it uploads are artifacts of the run, not a tag, a PyPI release or a GitHub release.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

_ROOT: Path = Path(__file__).resolve().parents[2]
_SCRIPT: Path = _ROOT / "scripts" / "desktop_artifact.sh"
_LATEST: Path = _ROOT / "scripts" / "desktop_latest.py"
_WORKFLOW: Path = _ROOT / ".github" / "workflows" / "desktop-artifacts.yml"
_SIGNING: tuple[str, ...] = (
    "APPLE_CERTIFICATE",
    "APPLE_CERTIFICATE_PASSWORD",
    "APPLE_SIGNING_IDENTITY",
    "APPLE_ID",
    "APPLE_PASSWORD",
    "APPLE_TEAM_ID",
    "APPLE_API_KEY",
    "APPLE_API_ISSUER",
    "APPLE_API_KEY_PATH",
    "TAURI_SIGNING_PRIVATE_KEY",
    "TAURI_SIGNING_PRIVATE_KEY_PASSWORD",
)


def _env(**extra: str) -> dict[str, str]:
    env: dict[str, str] = {key: value for key, value in os.environ.items() if key not in _SIGNING}
    env.update(extra)
    return env


def test_signing_is_skipped_when_the_secrets_are_absent() -> None:
    result = subprocess.run(
        ["bash", str(_SCRIPT), "--signing-plan"],
        check=False,
        capture_output=True,
        text=True,
        env=_env(),
    )
    assert result.returncode == 0, result.stderr
    assert "absent" in result.stdout
    assert "skipped" in result.stdout


def test_a_present_signing_secret_is_not_used_or_printed() -> None:
    result = subprocess.run(
        ["bash", str(_SCRIPT), "--signing-plan"],
        check=False,
        capture_output=True,
        text=True,
        env=_env(APPLE_CERTIFICATE="secret-value"),
    )
    assert result.returncode == 0, result.stderr
    assert "skipped" in result.stdout
    assert "secret-value" not in result.stdout + result.stderr


def test_the_artifact_script_does_not_sign_notarize_or_publish() -> None:
    text: str = _SCRIPT.read_text(encoding="utf-8")
    assert "--no-sign" in text
    assert "--bundles dmg" in text
    assert "fetch-tools.sh" in text
    # ``--signing-plan`` is this script's own switch. ``--sign`` as its own argument is the one that signs.
    unsigned: str = text.replace("--no-sign", "")
    assert "--sign " not in unsigned
    assert not any(line.strip().endswith("--sign") for line in unsigned.splitlines())
    for forbidden in ("notarytool", "gh release", "git tag", "uv publish", "softprops/action-gh-release"):
        assert forbidden not in text
    syntax = subprocess.run(["bash", "-n", str(_SCRIPT)], check=False, capture_output=True, text=True)
    assert syntax.returncode == 0, syntax.stderr


def test_building_the_image_is_refused_off_a_mac() -> None:
    if os.uname().sysname == "Darwin":
        return
    result = subprocess.run(
        ["bash", str(_SCRIPT)],
        check=False,
        capture_output=True,
        text=True,
        env=_env(),
        cwd=_ROOT,
    )
    assert result.returncode != 0
    assert "Mac" in result.stderr


def test_latest_json_names_the_disk_image_and_is_not_a_release(tmp_path: Path) -> None:
    destination: Path = tmp_path / "latest.json"
    result = subprocess.run(
        [sys.executable, str(_LATEST), str(destination), "0.1.0", "Dot_0.1.0_universal.dmg"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    loaded: object = json.loads(destination.read_text(encoding="utf-8"))
    assert isinstance(loaded, dict)
    assert loaded["version"] == "0.1.0"
    assert "unsigned" in str(loaded["notes"]).lower()
    platforms: object = loaded["platforms"]
    assert isinstance(platforms, dict)
    for name in ("darwin-aarch64", "darwin-x86_64"):
        entry: object = platforms[name]
        assert isinstance(entry, dict)
        assert entry["signature"] == ""
        assert entry["url"] == "Dot_0.1.0_universal.dmg"
        assert "releases/" not in str(entry["url"])
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", str(loaded["pub_date"]))


def test_the_artifact_workflow_is_manual_and_publishes_nothing() -> None:
    text: str = _WORKFLOW.read_text(encoding="utf-8")
    loaded: object = yaml.safe_load(text)
    assert isinstance(loaded, dict)
    # PyYAML reads the key ``on`` as True.
    assert set(loaded[True]) == {"workflow_dispatch"}
    assert loaded["permissions"] == {"contents": "read"}
    jobs: object = loaded["jobs"]
    assert isinstance(jobs, dict)
    job: object = jobs["bundle"]
    assert isinstance(job, dict)
    assert job["runs-on"] == "macos-latest"
    assert job["permissions"] == {"contents": "read", "actions": "write"}
    assert "id-token" not in json.dumps(job["permissions"])
    steps: object = job["steps"]
    assert isinstance(steps, list)
    checkouts = [step for step in steps if isinstance(step, dict) and "actions/checkout@" in str(step.get("uses", ""))]
    assert checkouts and all(step.get("with", {}).get("persist-credentials") is False for step in checkouts)
    uploads = [step for step in steps if isinstance(step, dict) and "actions/upload-artifact@" in str(step.get("uses"))]
    assert len(uploads) == 1
    assert uploads[0].get("with", {}).get("if-no-files-found") == "error"
    for step in steps:
        assert isinstance(step, dict)
        if "uses" in step:
            assert re.search(r"@[0-9a-f]{40}\b", str(step["uses"]))
        assert "${{" not in str(step.get("run", ""))
    for forbidden in ("gh release", "git tag", "uv publish", "softprops/action-gh-release", "contents: write"):
        assert forbidden not in text
