"""The Dot.app that runs and whether macOS can give it the microphone (item 636), and System Settings shown where the
owner allows it. The owner pressed the composer's microphone in a Dot.app built before item 594 and read that macOS had
not given it the microphone, with Dot switched on in System Settings: that Dot.app names no reason for the microphone
and has no entitlement to it, so WebKit refuses it before macOS is asked, and no switch helps.

The app is a bundle written in the test's folder, `codesign` is a stand-in that says what the real one says of a
signature (its words are the real ones, read from a Dot.app and from throwaway bundles signed ad hoc), the running apps
are given, and the opener is the test's own: nothing here reads the owner's Dot.app or shows System Settings. Whatever
a test forgets to give an opener, `DOT_OPEN` names a program that is not there."""

import os
import plistlib
import stat
import subprocess
from collections.abc import Sequence
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from dotagent.core.errors import DesktopError, DotError
from dotagent.server.app import status_for
from dotagent.server.routes_voice import voice_router
from dotagent.voice.desktopapp import AUDIO_INPUT, DesktopApp, open_url, running_apps, settings_url

MICROPHONE_REASON: str = "Dot listens only while its microphone is on, to write what you say into the message box."
SPEECH_REASON: str = "Dot turns what you say into text in the message box. macOS may send your voice to Apple."
MICROPHONE_PANE: str = "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
SPEECH_PANE: str = "x-apple.systempreferences:com.apple.preference.security?Privacy_SpeechRecognition"
# What `codesign -dv` printed for the owner's Dot.app (a hardened Developer ID build of 2026-10-20), but for the flags.
CODESIGN_SAID: str = """Executable={app}/Contents/MacOS/dot-desktop
Identifier=dev.dot.desktop
Format=app bundle with Mach-O universal (x86_64 arm64)
CodeDirectory v=20500 size=36187 flags={flags} hashes=1124+3 location=embedded
Signature size=8973
Timestamp=20. Oct 2026 at 6.41.56
Info.plist entries=14
TeamIdentifier=79DZ4AA4DW
Runtime Version=27.0.0
Sealed Resources version=2 rules=13 files=1
Internal requirements count=1 size=176
"""
# What `codesign -d --entitlements - --xml` printed for a bundle signed with the microphone entitlement: one line.
ENTITLED_XML: str = (
    '<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
    '"https://www.apple.com/DTDs/PropertyList-1.0.dtd"><plist version="1.0"><dict>'
    "<key>com.apple.security.device.audio-input</key><true/></dict></plist>\n"
)
ENTITLED: dict[str, object] = {AUDIO_INPUT: True}
HERE: str = "127.0.0.1:8765"


class Codesign:
    """A stand-in for `codesign`: it says what the real one says of a signature with `flags` and `entitlements`
    (nothing at all when there are none), or that the code is not signed; `asked` holds each command it was given."""

    def __init__(
        self, flags: str = "0x10000(runtime)", entitlements: dict[str, object] | None = None, signed: bool = True
    ) -> None:
        self.flags: str = flags
        self.entitlements: dict[str, object] | None = entitlements
        self.signed: bool = signed
        self.asked: list[list[str]] = []

    def entitlements_said(self) -> str:
        return "" if self.entitlements is None else plistlib.dumps(self.entitlements).decode("utf-8")

    def __call__(self, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        command: list[str] = list(arguments)
        self.asked.append(command)
        app: str = command[-1]
        if not self.signed:
            return subprocess.CompletedProcess(command, 1, "", f"{app}: code object is not signed at all\n")
        # The real one prints everything to stderr but the entitlements, which `--entitlements -` writes to stdout.
        if "--entitlements" in command:
            return subprocess.CompletedProcess(
                command, 0, self.entitlements_said(), f"Executable={app}/Contents/MacOS/dot-desktop\n"
            )
        return subprocess.CompletedProcess(command, 0, "", CODESIGN_SAID.format(app=app, flags=self.flags))


def make_app(
    folder: Path,
    microphone: str | None = MICROPHONE_REASON,
    speech: str | None = SPEECH_REASON,
    name: str = "Dot.app",
) -> Path:
    """A bundle with the `Info.plist` of an app that gives `microphone` and `speech` as its reasons (a reason left out
    when it is None), and the seal a signature leaves."""
    app: Path = folder / name
    (app / "Contents" / "_CodeSignature").mkdir(parents=True)
    info: dict[str, object] = {"CFBundleIdentifier": "dev.dot.desktop", "CFBundleName": "Dot"}
    if microphone is not None:
        info["NSMicrophoneUsageDescription"] = microphone
    if speech is not None:
        info["NSSpeechRecognitionUsageDescription"] = speech
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
    (app / "Contents" / "_CodeSignature" / "CodeResources").write_bytes(b"seal")
    return app


def later(file: Path) -> None:
    """`file` as a program that wrote it a moment after the last one did."""
    modified: int = file.stat().st_mtime_ns + 2_000_000_000
    os.utime(file, ns=(modified, modified))


def desk(*apps: Path, run: Codesign | None = None) -> DesktopApp:
    """A reader of the desktop app for which `apps` run."""
    return DesktopApp(running=lambda _bundle_id: list(apps), run=Codesign() if run is None else run)


@pytest.fixture(autouse=True)
def _nothing_is_opened(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A router made without an opener runs `open_url`, which runs the program `DOT_OPEN` names: one not there."""
    monkeypatch.setenv("DOT_OPEN", str(tmp_path / "no-such-open"))
    monkeypatch.setenv("DOT_HOME", str(tmp_path / "home"))


# ----- the Dot.app that runs listens, or does not -----------------------------------------------------------------

LISTENS: list[tuple[str | None, str | None, str, dict[str, object] | None, bool]] = [
    # (microphone reason, speech reason, flags of the signature, its entitlements, whether the app listens)
    (MICROPHONE_REASON, SPEECH_REASON, "0x10000(runtime)", ENTITLED, True),
    (MICROPHONE_REASON, SPEECH_REASON, "0x10002(adhoc,runtime)", ENTITLED, True),
    (MICROPHONE_REASON, SPEECH_REASON, "0x2(adhoc)", None, True),
    (MICROPHONE_REASON, SPEECH_REASON, "0x0(none)", None, True),
    # An app that names its reasons but is hardened without the entitlement: macOS gives it no microphone.
    (MICROPHONE_REASON, SPEECH_REASON, "0x10000(runtime)", None, False),
    (MICROPHONE_REASON, SPEECH_REASON, "0x10000(runtime)", {AUDIO_INPUT: False}, False),
    (MICROPHONE_REASON, SPEECH_REASON, "0x10000(runtime)", {"com.apple.security.device.camera": True}, False),
    (MICROPHONE_REASON, SPEECH_REASON, "0x10000(runtime)", {AUDIO_INPUT: "true"}, False),
    # WebKit refuses an app that does not say why it asks, whatever it is entitled to.
    (None, SPEECH_REASON, "0x10000(runtime)", ENTITLED, False),
    (MICROPHONE_REASON, None, "0x10000(runtime)", ENTITLED, False),
    ("", SPEECH_REASON, "0x10000(runtime)", ENTITLED, False),
    (MICROPHONE_REASON, "  \n", "0x10000(runtime)", ENTITLED, False),
    (None, None, "0x2(adhoc)", None, False),
    # The Dot.app of 2026-10-20 the owner runs: no reason, hardened, no entitlement.
    (None, None, "0x10000(runtime)", None, False),
]


@pytest.mark.parametrize(("microphone", "speech", "flags", "entitlements", "listens"), LISTENS)
def test_an_app_listens_when_it_says_why_it_asks_and_the_hardened_runtime_lets_it(
    tmp_path: Path,
    microphone: str | None,
    speech: str | None,
    flags: str,
    entitlements: dict[str, object] | None,
    listens: bool,
) -> None:
    app: Path = make_app(tmp_path, microphone, speech)
    reader: DesktopApp = desk(app, run=Codesign(flags, entitlements))
    assert reader.listens(app) is listens
    assert reader.state() == ("ready" if listens else "old")


def test_codesign_is_asked_only_for_what_the_answer_needs(tmp_path: Path) -> None:
    # An app with a reason missing is old whatever its signature says: the signature is not read.
    nameless: Codesign = Codesign()
    assert desk(make_app(tmp_path, None, SPEECH_REASON, "A.app"), run=nameless).state() == "old"
    assert nameless.asked == []
    # No hardened runtime keeps nothing from the app: the entitlements are not read.
    plain: Codesign = Codesign("0x2(adhoc)")
    app: Path = make_app(tmp_path, name="B.app")
    assert desk(app, run=plain).state() == "ready"
    assert plain.asked == [["/usr/bin/codesign", "-dv", str(app)]]
    # A hardened one is asked for its entitlements, by the command the release's check uses.
    hardened: Codesign = Codesign(entitlements=ENTITLED)
    other: Path = make_app(tmp_path, name="C.app")
    assert desk(other, run=hardened).state() == "ready"
    assert hardened.asked == [
        ["/usr/bin/codesign", "-dv", str(other)],
        ["/usr/bin/codesign", "-d", "--entitlements", "-", "--xml", str(other)],
    ]


def test_the_entitlements_codesign_printed_on_one_line_are_read_as_they_come(tmp_path: Path) -> None:
    class OneLine(Codesign):
        def entitlements_said(self) -> str:
            return ENTITLED_XML

    assert desk(make_app(tmp_path), run=OneLine()).state() == "ready"


def test_an_app_that_is_not_signed_has_no_hardened_runtime_to_keep_the_microphone_from_it(tmp_path: Path) -> None:
    assert desk(make_app(tmp_path), run=Codesign(signed=False)).state() == "ready"
    assert desk(make_app(tmp_path, None, None, "B.app"), run=Codesign(signed=False)).state() == "old"


def test_the_dot_app_that_runs_is_old_ready_or_not_found(tmp_path: Path) -> None:
    old: Path = make_app(tmp_path, None, None, "Old.app")
    new: Path = make_app(tmp_path, name="New.app")
    signed: Codesign = Codesign(entitlements=ENTITLED)
    assert desk(run=signed).state() == "unknown"
    assert desk(old, run=signed).state() == "old"
    assert desk(new, run=signed).state() == "ready"
    # Two run: one that listens is enough, since the page cannot be told from the other.
    assert desk(old, new, run=signed).state() == "ready"
    assert desk(old, old, run=signed).state() == "old"


def test_the_reader_asks_for_the_desktop_app_by_its_bundle_identifier() -> None:
    asked: list[str] = []

    def running(bundle_id: str) -> list[Path]:
        asked.append(bundle_id)
        return []

    assert DesktopApp(running=running).state() == "unknown"
    assert asked == ["dev.dot.desktop"]
    assert DesktopApp("dev.example.other", running=running).state() == "unknown"
    assert asked == ["dev.dot.desktop", "dev.example.other"]


def test_no_app_runs_under_a_bundle_identifier_nobody_has() -> None:
    # The real NSRunningApplication, asked for an app that cannot be running.
    assert running_apps("dev.dot.no-such-app-636") == ()


# ----- what was read of an app is kept while its files stay as they were ------------------------------------------


def test_what_was_read_of_an_app_is_kept_until_its_info_plist_or_its_signature_changes(tmp_path: Path) -> None:
    app: Path = make_app(tmp_path)
    codesign: Codesign = Codesign(entitlements=ENTITLED)
    reader: DesktopApp = desk(app, run=codesign)
    assert reader.state() == "ready"
    read: int = len(codesign.asked)
    assert read == 2
    # The pane is asked for at each press: the same files are not read again.
    assert [reader.state() for _ in range(3)] == ["ready"] * 3
    assert len(codesign.asked) == read
    # A new Info.plist in place (the updater put a build there) is read again.
    later(app / "Contents" / "Info.plist")
    assert reader.state() == "ready"
    assert len(codesign.asked) == 2 * read
    # So is an app signed again with nothing else changed: its seal is written anew.
    codesign.entitlements = None
    later(app / "Contents" / "_CodeSignature" / "CodeResources")
    assert reader.state() == "old"
    assert len(codesign.asked) == 3 * read


def test_an_old_app_replaced_by_a_new_one_in_place_is_judged_anew(tmp_path: Path) -> None:
    """The daemon runs for days: the owner updates Dot.app, and the next press must not be told it is old."""
    app: Path = make_app(tmp_path, None, None)
    reader: DesktopApp = desk(app, run=Codesign(entitlements=ENTITLED))
    assert reader.state() == "old"
    (app / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps(
            {"NSMicrophoneUsageDescription": MICROPHONE_REASON, "NSSpeechRecognitionUsageDescription": SPEECH_REASON}
        )
    )
    later(app / "Contents" / "Info.plist")
    assert reader.state() == "ready"


def test_two_apps_at_two_places_are_read_on_their_own(tmp_path: Path) -> None:
    old: Path = make_app(tmp_path, None, None, "Old.app")
    new: Path = make_app(tmp_path, name="New.app")
    reader: DesktopApp = desk(old, new, run=Codesign(entitlements=ENTITLED))
    assert (reader.listens(old), reader.listens(new)) == (False, True)


# ----- what cannot be read is an error that says what, never an answer --------------------------------------------


def test_an_app_whose_info_plist_is_missing_or_unreadable_is_an_error_that_names_it(tmp_path: Path) -> None:
    unreadable: str = r"Info\.plist of the desktop app could not be read"
    gone: Path = tmp_path / "Gone.app"
    gone.mkdir()
    with pytest.raises(DesktopError, match=unreadable) as missing:
        desk(gone).state()
    assert missing.value.context["app"] == str(gone)
    broken: Path = make_app(tmp_path, name="Broken.app")
    (broken / "Contents" / "Info.plist").write_bytes(b"this is no property list")
    with pytest.raises(DesktopError, match=unreadable) as garbled:
        desk(broken).state()
    assert garbled.value.context["file"] == str(broken / "Contents" / "Info.plist")
    # A reason that is not words is not read as none.
    odd: Path = make_app(tmp_path, name="Odd.app")
    (odd / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"NSMicrophoneUsageDescription": 5}))
    with pytest.raises(DesktopError, match=unreadable):
        desk(odd).state()


def test_a_codesign_that_fails_or_says_something_else_is_an_error_with_what_it_said(tmp_path: Path) -> None:
    app: Path = make_app(tmp_path)

    def fails(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(arguments), 3, "", "codesign: something went wrong\n")

    with pytest.raises(DesktopError, match="could not read the signature") as failed:
        DesktopApp(running=lambda _id: [app], run=fails).state()
    assert failed.value.context["code"] == 3 and "something went wrong" in str(failed.value.context["said"])

    def silent(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(list(arguments), 0, "", "Executable=/x\nIdentifier=dev.dot.desktop\n")

    with pytest.raises(DesktopError, match="did not say the flags"):
        DesktopApp(running=lambda _id: [app], run=silent).state()

    class EntitlementsFail(Codesign):
        def __call__(self, arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
            if "--entitlements" in arguments:
                return subprocess.CompletedProcess(list(arguments), 1, "", "codesign: no entitlements for you\n")
            return super().__call__(arguments)

    with pytest.raises(DesktopError, match="could not read the entitlements"):
        desk(app, run=EntitlementsFail()).state()

    class Garbled(Codesign):
        def entitlements_said(self) -> str:
            return "<plist><dict>"

    with pytest.raises(DesktopError, match="entitlements of the desktop app could not be read"):
        desk(app, run=Garbled()).state()


def test_an_answer_that_could_not_be_read_is_not_kept(tmp_path: Path) -> None:
    app: Path = make_app(tmp_path)
    flaky: Codesign = Codesign(entitlements=ENTITLED)
    reader: DesktopApp = desk(app, run=flaky)
    flaky.flags = "no flags at all"
    with pytest.raises(DesktopError):
        reader.state()
    flaky.flags = "0x10000(runtime)"
    assert reader.state() == "ready"


# ----- System Settings is shown by `open` -------------------------------------------------------------------------


def stand_in_open(folder: Path, exit_code: int = 0, said: str = "") -> Path:
    """A program for `DOT_OPEN` that writes down what it was given in `argv` beside it, says `said` on stderr and ends
    with `exit_code`."""
    folder.mkdir()
    program: Path = folder / "open"
    program.write_text(
        f'#!/bin/sh\nprintf \'%s\\n\' "$@" > "{folder}/argv"\nprintf \'%s\' "{said}" >&2\nexit {exit_code}\n',
        encoding="utf-8",
    )
    program.chmod(program.stat().st_mode | stat.S_IXUSR)
    return program


def test_the_panes_are_the_privacy_panes_of_the_microphone_and_of_speech_recognition() -> None:
    assert settings_url("microphone") == MICROPHONE_PANE
    assert settings_url("speech") == SPEECH_PANE


def test_open_hands_the_address_to_the_program_dot_open_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOT_OPEN", str(stand_in_open(tmp_path / "bin")))
    open_url(MICROPHONE_PANE)
    assert (tmp_path / "bin" / "argv").read_text(encoding="utf-8") == f"{MICROPHONE_PANE}\n"


def test_an_open_that_fails_or_is_not_there_is_an_error_that_says_what_was_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOT_OPEN", str(stand_in_open(tmp_path / "bin", 1, "The application cannot be opened.")))
    with pytest.raises(DesktopError, match="macOS could not open System Settings") as failed:
        open_url(SPEECH_PANE)
    assert failed.value.context == {"url": SPEECH_PANE, "code": 1, "stderr": "The application cannot be opened."}
    # What `_nothing_is_opened` set: a program that is not there.
    monkeypatch.setenv("DOT_OPEN", str(tmp_path / "no-such-open"))
    with pytest.raises(DesktopError, match="open could not be run") as absent:
        open_url(SPEECH_PANE)
    assert absent.value.context == {"command": f"{tmp_path / 'no-such-open'} {SPEECH_PANE}"}


# ----- the route --------------------------------------------------------------------------------------------------


class Settings:
    """The opener of a test: it writes down what it was asked to show, and shows nothing."""

    def __init__(self) -> None:
        self.shown: list[str] = []

    def __call__(self, url: str) -> None:
        self.shown.append(url)


class Running:
    """The apps that run, as the reader asks for them; `asked` counts the times it did."""

    def __init__(self, *apps: Path) -> None:
        self.apps: list[Path] = list(apps)
        self.asked: int = 0

    def __call__(self, _bundle_id: str) -> list[Path]:
        self.asked += 1
        return self.apps


def server(running: Running, settings: Settings) -> httpx.AsyncClient:
    """The voice routes over the stand-ins, asked as a page asks, with the error answers of the daemon's application
    and the address of this Mac, which the guard lets through."""
    app: FastAPI = FastAPI()
    reader: DesktopApp = DesktopApp(running=running, run=Codesign(entitlements=ENTITLED))
    app.include_router(voice_router(reader, settings))

    @app.exception_handler(DotError)
    async def dot_error(_request: Request, error: DotError) -> JSONResponse:
        return JSONResponse({"message": error.message, "context": error.context}, status_code=status_for(error))

    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=f"http://{HERE}")


@pytest.fixture
def ready_app(tmp_path: Path) -> Path:
    return make_app(tmp_path, name="New.app")


@pytest.fixture
def old_app(tmp_path: Path) -> Path:
    return make_app(tmp_path, None, None, "Old.app")


@pytest.fixture
def unreadable_app(tmp_path: Path) -> Path:
    app: Path = tmp_path / "Gone.app"
    app.mkdir()
    return app


@pytest.mark.parametrize(("pane", "url"), [("microphone", MICROPHONE_PANE), ("speech", SPEECH_PANE)])
async def test_a_dot_app_that_asks_macos_has_system_settings_shown_at_the_pane(
    ready_app: Path, pane: str, url: str
) -> None:
    settings: Settings = Settings()
    async with server(Running(ready_app), settings) as client:
        answer: httpx.Response = await client.post("/api/voice/allow", json={"pane": pane})
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"opened": True, "app": "ready"}
    assert settings.shown == [url]


async def test_a_dot_app_built_before_it_asked_macos_has_nothing_shown_and_the_answer_says_so(old_app: Path) -> None:
    settings: Settings = Settings()
    async with server(Running(old_app), settings) as client:
        for pane in ("microphone", "speech"):
            answer: httpx.Response = await client.post("/api/voice/allow", json={"pane": pane})
            assert answer.status_code == 200, answer.text
            assert answer.json() == {"opened": False, "app": "old"}
    assert settings.shown == []


async def test_no_dot_app_found_running_is_unknown_and_system_settings_is_shown_all_the_same() -> None:
    settings: Settings = Settings()
    async with server(Running(), settings) as client:
        answer: httpx.Response = await client.post("/api/voice/allow", json={"pane": "speech"})
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"opened": True, "app": "unknown"}
    assert settings.shown == [SPEECH_PANE]


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765", "LOCALHOST", "[::1]:8765"])
async def test_the_name_of_this_macs_own_address_is_let_through(ready_app: Path, host: str) -> None:
    settings: Settings = Settings()
    async with server(Running(ready_app), settings) as client:
        answer: httpx.Response = await client.post(
            "/api/voice/allow", json={"pane": "microphone"}, headers={"Host": host}
        )
    assert answer.status_code == 200, answer.text
    assert settings.shown == [MICROPHONE_PANE]


@pytest.mark.parametrize(
    "host", ["mac.tail1234.ts.net", "192.168.1.5:8765", "dot.example:8765", "127.0.0.1.example.com"]
)
async def test_a_request_that_did_not_come_to_this_macs_own_address_is_refused_before_anything_is_read(
    ready_app: Path, host: str
) -> None:
    settings: Settings = Settings()
    running: Running = Running(ready_app)
    async with server(running, settings) as client:
        answer: httpx.Response = await client.post(
            "/api/voice/allow", json={"pane": "microphone"}, headers={"Host": host}
        )
    assert answer.status_code == 422, answer.text
    assert "System Settings opens on the Mac itself" in answer.json()["message"]
    assert settings.shown == [] and running.asked == 0


@pytest.mark.parametrize("body", [{"pane": "camera"}, {"pane": "Microphone"}, {}, {"pane": "microphone", "url": "x"}])
async def test_only_the_two_panes_can_be_asked_for_and_nothing_else_is_taken_from_the_request(
    ready_app: Path, body: dict[str, str]
) -> None:
    settings: Settings = Settings()
    async with server(Running(ready_app), settings) as client:
        answer: httpx.Response = await client.post("/api/voice/allow", json=body)
    assert answer.status_code == 422, answer.text
    assert settings.shown == []


async def test_a_settings_that_cannot_be_shown_is_an_error_not_an_answer(ready_app: Path) -> None:
    class Refusing(Settings):
        def __call__(self, url: str) -> None:
            raise DesktopError("macOS could not open System Settings", {"url": url})

    async with server(Running(ready_app), Refusing()) as client:
        answer: httpx.Response = await client.post("/api/voice/allow", json={"pane": "microphone"})
    assert answer.status_code == 500
    assert answer.json()["message"] == "macOS could not open System Settings"


async def test_an_app_that_cannot_be_read_is_an_error_and_nothing_is_shown(unreadable_app: Path) -> None:
    settings: Settings = Settings()
    async with server(Running(unreadable_app), settings) as client:
        answer: httpx.Response = await client.post("/api/voice/allow", json={"pane": "microphone"})
    assert answer.status_code == 500 and "Info.plist" in answer.json()["message"]
    assert settings.shown == []
