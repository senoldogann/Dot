"""What ``uv pip sync --require-hashes`` does with a hash, which is the check the desktop installer now runs (the
installer is Rust and is tested in ``desktop/src-tauri``). A requirements entry whose hash is not the file's makes the
sync refuse it; the hash of the file installs it. ``uv pip install --constraints`` does not: a wrong hash there is
installed all the same, which is why a put-back of an older version, the one path that still uses ``--constraints``,
does not claim to check hashes."""

import hashlib
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

_UV = shutil.which("uv")


def _wheel(directory: Path) -> tuple[Path, str]:
    """A tiny pure-Python wheel and the SHA-256 of its bytes."""
    build = directory / "build"
    package = build / "tiny"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("x = 1\n", encoding="utf-8")
    dist = build / "tiny-1.0.0.dist-info"
    dist.mkdir()
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: tiny\nVersion: 1.0.0\n", encoding="utf-8")
    (dist / "WHEEL").write_text(
        "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        encoding="utf-8",
    )
    records: list[str] = []
    for path in sorted(build.rglob("*")):
        if path.is_file():
            data = path.read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            records.append(f"{path.relative_to(build).as_posix()},sha256={digest},{len(data)}")
    records.append("tiny-1.0.0.dist-info/RECORD,,")
    (dist / "RECORD").write_text("\n".join(records) + "\n", encoding="utf-8")
    wheel = directory / "tiny-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for path in build.rglob("*"):
            if path.is_file():
                archive.write(path, path.relative_to(build).as_posix())
    return wheel, hashlib.sha256(wheel.read_bytes()).hexdigest()


def _run(uv: str, *args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run((uv, "--no-config", *args), cwd=cwd, capture_output=True, text=True, check=False)


@pytest.mark.skipif(_UV is None, reason="uv is not on PATH")
def test_a_wrong_hash_makes_the_sync_refuse_the_wheel_and_the_right_hash_installs_it(tmp_path: Path) -> None:
    assert _UV is not None
    wheel, digest = _wheel(tmp_path / "bundle")
    wrong = "0" * 64
    (wheel.parent / "wrong.txt").write_text(f"./{wheel.name} --hash=sha256:{wrong}\n", encoding="utf-8")
    (wheel.parent / "right.txt").write_text(f"./{wheel.name} --hash=sha256:{digest}\n", encoding="utf-8")
    env = tmp_path / "env"
    made = _run(_UV, "venv", "--no-project", "--python", sys.executable, str(env), cwd=tmp_path)
    assert made.returncode == 0, made.stderr
    refused = _run(
        _UV,
        "pip",
        "sync",
        "--require-hashes",
        "--default-index",
        "https://pypi.org/simple",
        "--python",
        str(env),
        str(wheel.parent / "wrong.txt"),
        cwd=wheel.parent,
    )
    assert refused.returncode != 0
    assert "hash mismatch" in refused.stderr.lower()
    assert not (env / "lib").exists() or not any((env / "lib").rglob("tiny-1.0.0.dist-info"))
    accepted = _run(
        _UV,
        "pip",
        "sync",
        "--require-hashes",
        "--default-index",
        "https://pypi.org/simple",
        "--python",
        str(env),
        str(wheel.parent / "right.txt"),
        cwd=wheel.parent,
    )
    assert accepted.returncode == 0, accepted.stderr
    assert any((env / "lib").rglob("tiny-1.0.0.dist-info"))
    # The put-back still uses --constraints, and uv does not check a hash there.
    pinned = tmp_path / "pins.txt"
    pinned.write_text(f"tiny==1.0.0 --hash=sha256:{wrong}\n", encoding="utf-8")
    constrained = _run(
        _UV,
        "pip",
        "install",
        "--python",
        str(env),
        "--constraints",
        str(pinned),
        str(wheel),
        cwd=tmp_path,
    )
    assert constrained.returncode == 0, constrained.stderr


def test_the_release_checker_accepts_a_hashed_file_and_refuses_one_without(tmp_path: Path) -> None:
    """The checker ``desktop/release.sh`` runs over the requirements file, lifted out so it can be tried here."""
    script = Path(__file__).resolve().parents[2] / "desktop" / "release.sh"
    text = script.read_text(encoding="utf-8")
    marker = "<<'PY'"
    body = text[text.index(marker) + len(marker) :].split("\n", 1)[1].split("\nPY\n", 1)[0]
    checker = tmp_path / "check.py"
    checker.write_text(body, encoding="utf-8")
    good = tmp_path / "requirements.txt"
    digest = "ab" * 32
    good.write_text(
        "fastapi==0.143.0 \\\n    --hash=sha256:" + digest + "\n./dotagent_macos-0.1.0-py3-none-any.whl"
        f" --hash=sha256:{digest}\n",
        encoding="utf-8",
    )
    ran = subprocess.run(
        (sys.executable, str(checker), str(good), "dotagent_macos-0.1.0-py3-none-any.whl", digest),
        capture_output=True,
        text=True,
        check=False,
    )
    assert ran.returncode == 0, ran.stderr
    missing = tmp_path / "missing.txt"
    missing.write_text(
        "fastapi==0.143.0\n./dotagent_macos-0.1.0-py3-none-any.whl --hash=sha256:" + digest + "\n", encoding="utf-8"
    )
    ran = subprocess.run(
        (sys.executable, str(checker), str(missing), "dotagent_macos-0.1.0-py3-none-any.whl", digest),
        capture_output=True,
        text=True,
        check=False,
    )
    assert ran.returncode != 0
    assert "no hash" in ran.stderr
