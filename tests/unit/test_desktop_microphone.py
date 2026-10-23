"""The desktop app's microphone (item 594): the app tells macOS why it asks for the microphone and for speech
recognition, which the composer's dictation needs (item 574), and is entitled under the hardened runtime to the
microphone alone. Without these, macOS gives the app neither, and the composer says so (item 593)."""

import json
import plistlib
import re
from pathlib import Path

_ROOT: Path = Path(__file__).resolve().parents[2]
_SHELL: Path = _ROOT / "desktop" / "src-tauri"
_STATIC: Path = _ROOT / "src" / "dotagent" / "server" / "static"
# A frame the web UI makes, with the attributes it is made with.
_FRAME: re.Pattern[str] = re.compile(r'el\(\s*"iframe"\s*,\s*\{(?P<attributes>[^}]*)\}')


def test_the_app_says_why_it_asks_for_the_microphone_and_speech_recognition() -> None:
    # Tauri merges an Info.plist beside its configuration into the app's own.
    info: dict[str, object] = plistlib.loads((_SHELL / "Info.plist").read_bytes())
    assert set(info) == {"NSMicrophoneUsageDescription", "NSSpeechRecognitionUsageDescription"}
    assert all(isinstance(reason, str) and reason.startswith("Dot ") for reason in info.values())


def test_the_hardened_runtime_stays_on_with_the_microphone_its_one_exception() -> None:
    tauri: dict[str, dict[str, dict[str, object]]] = json.loads((_SHELL / "tauri.conf.json").read_text())
    macos: dict[str, object] = tauri["bundle"]["macOS"]
    assert macos["hardenedRuntime"] is True
    entitlements: dict[str, object] = plistlib.loads((_SHELL / str(macos["entitlements"])).read_bytes())
    # No camera, no JIT, no exception to library validation: a new key here is a decision of its own.
    assert entitlements == {"com.apple.security.device.audio-input": True}


def test_the_one_window_asks_the_shell_before_it_gives_a_page_anything() -> None:
    """The independent security review of item 594: without the handler, wry grants every request no one answers, the
    microphone of a page a link led the window to among them, and no test of `permission_for` alone would fail."""
    source: str = (_SHELL / "src" / "lib.rs").read_text(encoding="utf-8")
    assert source.count("WebviewWindowBuilder::new(") == 1
    built: str = source[source.index("fn window(") :]
    built = built[: built.index(".build()")]
    assert ".on_permission_request(|webview, kind| {" in built
    assert "permission_for(page.as_ref(), kind, &addresses)" in built


def test_every_frame_of_the_ui_is_sandboxed_and_given_no_permission() -> None:
    """The handler is told what is asked, not by which frame, and judges the window's page (the security review of item
    594): a frame of the daemon's own address would be judged as its page is. The UI's frames are sandboxed without
    `allow-same-origin`, so their origin is opaque and WebKit refuses them before the shell is asked, and none is handed
    a permission (`allow=`). The UI writes no markup (`test_static_ui.py`), so these are the only frames it makes."""
    made: list[str] = []
    for script in sorted(_STATIC.rglob("*.js")):
        text: str = script.read_text(encoding="utf-8")
        frames: list[re.Match[str]] = list(_FRAME.finditer(text))
        # A frame made otherwise (createElement, another spelling of el's call) names the tag too, and is caught here.
        assert len(re.findall(r"[\"'`]iframe[\"'`]", text)) == len(frames), script.name
        for frame in frames:
            attributes: str = frame.group("attributes")
            made.append(script.name)
            assert re.search(r'\bsandbox:\s*"allow-scripts"', attributes), (script.name, attributes)
            assert "allow-same-origin" not in attributes and not re.search(r"\ballow:", attributes), script.name
    assert sorted(made) == ["filepanel.js", "pageframe.js"]


def test_a_release_refuses_a_bundle_without_the_reasons_or_the_microphone() -> None:
    """The independent security review of item 594: the defect it mends, a Dot.app without the reasons or the
    entitlement, would have shipped unseen; the release checks the bundle it signed for both."""
    release: str = (_ROOT / "desktop" / "release.sh").read_text(encoding="utf-8")
    for reason in ("NSMicrophoneUsageDescription", "NSSpeechRecognitionUsageDescription"):
        assert reason in release
    assert "codesign --display --entitlements - --xml" in release
    assert "PlistBuddy -c 'Print :com.apple.security.device.audio-input'" in release
