"""The reader of the desktop app against the real `codesign` (item 636). Whether macOS can give Dot.app the microphone
turns on two things its signature says, the hardened runtime and the microphone entitlement, and on words of
`codesign` that no stand-in can vouch for. Here throwaway bundles are signed ad hoc in the test's folder (a copy of a
system program stands for the app's own, which `codesign` only reads) with and without each, and the reader says what
it makes of every one. Nothing of the owner's Dot.app is read, no identity or keychain is used, and the bundles are
never run."""

import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest

from dotagent.voice.desktopapp import AUDIO_INPUT, DesktopApp, Signature

CODESIGN: str = "/usr/bin/codesign"
PROGRAM: str = "/bin/echo"
REASONS: dict[str, str] = {
    "NSMicrophoneUsageDescription": "Dot listens only while its microphone is on.",
    "NSSpeechRecognitionUsageDescription": "Dot turns what you say into text. macOS may send your voice to Apple.",
}


def bundle(folder: Path, name: str, reasons: bool = True) -> Path:
    """An app of a copy of `PROGRAM` under the identifier of Dot.app, with its reasons for the microphone and speech
    recognition when `reasons`; not signed."""
    app: Path = folder / f"{name}.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    shutil.copy(PROGRAM, app / "Contents" / "MacOS" / "dot-desktop")
    info: dict[str, object] = {
        "CFBundleExecutable": "dot-desktop",
        "CFBundleIdentifier": "dev.dot.desktop",
        "CFBundleName": "Dot",
        "CFBundlePackageType": "APPL",
    }
    if reasons:
        info.update(REASONS)
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
    return app


def sign(app: Path, hardened: bool, entitlements: dict[str, object] | None = None) -> Path:
    """`app` signed ad hoc, with the hardened runtime when `hardened`, and with `entitlements` when it has any."""
    command: list[str] = [CODESIGN, "--force", "--sign", "-"]
    if hardened:
        command += ["--options", "runtime"]
    if entitlements is not None:
        file: Path = app.parent / f"{app.stem}.entitlements"
        file.write_bytes(plistlib.dumps(entitlements))
        command += ["--entitlements", str(file)]
    done: subprocess.CompletedProcess[str] = subprocess.run(
        [*command, str(app)], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr
    return app


def unsign(app: Path) -> Path:
    """`app` with no signature at all: the copy of the system program carries its own, which is taken off."""
    done: subprocess.CompletedProcess[str] = subprocess.run(
        [CODESIGN, "--remove-signature", str(app / "Contents" / "MacOS" / "dot-desktop")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    return app


def reader(app: Path) -> DesktopApp:
    """The reader with its own `codesign` and the app as the one that runs."""
    return DesktopApp(running=lambda _bundle_id: [app])


@pytest.mark.parametrize(
    ("hardened", "entitlements", "signature", "listens"),
    [
        (False, None, Signature(hardened=False, audio_input=False), True),
        (False, {AUDIO_INPUT: True}, Signature(hardened=False, audio_input=False), True),
        (True, None, Signature(hardened=True, audio_input=False), False),
        (True, {AUDIO_INPUT: True}, Signature(hardened=True, audio_input=True), True),
        (True, {AUDIO_INPUT: False}, Signature(hardened=True, audio_input=False), False),
        (True, {"com.apple.security.device.camera": True}, Signature(hardened=True, audio_input=False), False),
    ],
    ids=[
        "no hardened runtime",
        "no hardened runtime, entitled",
        "hardened, not entitled",
        "hardened, entitled to the microphone",
        "hardened, the microphone entitlement false",
        "hardened, entitled to the camera only",
    ],
)
def test_the_reader_reads_what_the_real_codesign_says_of_a_signature(
    tmp_path: Path, hardened: bool, entitlements: dict[str, object] | None, signature: Signature, listens: bool
) -> None:
    app: Path = sign(bundle(tmp_path, "Dot"), hardened, entitlements)
    assert reader(app).signature(app) == signature
    assert reader(app).state() == ("ready" if listens else "old")


def test_an_app_with_no_signature_at_all_is_not_under_the_hardened_runtime(tmp_path: Path) -> None:
    app: Path = unsign(bundle(tmp_path, "Dot"))
    assert reader(app).signature(app) == Signature(hardened=False, audio_input=False)
    assert reader(app).state() == "ready"


def test_an_app_that_names_no_reason_is_old_whatever_its_signature_says(tmp_path: Path) -> None:
    app: Path = sign(bundle(tmp_path, "Dot", reasons=False), True, {AUDIO_INPUT: True})
    assert reader(app).signature(app) == Signature(hardened=True, audio_input=True)
    assert reader(app).state() == "old"
