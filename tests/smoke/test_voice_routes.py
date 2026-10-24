"""The routes of the microphone's recording (item 626) on a real server: whether this Mac can write a recording
down, and writing one down, with a compiler and a program of the test's own in the place of Apple's
(``voice_stand_ins``).

An iPhone's page records the voice itself and sends it here as a WAV; the Mac writes it down on the device. These tests
read the routes as a page does: behind the token like every other, a Mac that cannot says why and writes nothing down,
a recording is read into a model before anything is done with it (the limits, a body that is no WAV, a language that is
no tag), what the program is given is a file of Dot's own making of mode 0600, no recording is left on disk, and what
fails says what to do, never a stack."""

import asyncio
import base64
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from voice_stand_ins import StandIn, make_stand_in, wav_of

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.views import _RECORDING_B64_CHARS
from dotagent.voice import helper
from dotagent.voice.toolchain import NO_COMPILER
from dotagent.voice.wav import MAX_RECORDING_SECONDS, RECORDING_RATE, TAIL_SECONDS

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "voice-routes-token"
_TAIL_SAMPLES: int = round(TAIL_SECONDS * RECORDING_RATE)
_WAIT_S: float = 30.0
# A recording of a second of a tone, as the page writes it.
SAMPLES: list[int] = [round(9000 * ((index // 20) % 2 * 2 - 1)) for index in range(RECORDING_RATE)]


def _silence(seconds: int) -> bytes:
    """A WAV of ``seconds`` of silence, made without packing each sample."""
    wav: bytes = wav_of([0])
    pcm: int = seconds * RECORDING_RATE * 2
    return wav[:4] + (36 + pcm).to_bytes(4, "little") + wav[8:40] + pcm.to_bytes(4, "little") + bytes(pcm)


def _body(wav: bytes, language: str = "tr-TR") -> dict[str, str]:
    return {"language": language, "data_b64": base64.b64encode(wav).decode("ascii")}


@pytest.fixture(scope="module")
def home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("home")


@pytest.fixture(scope="module")
def stand_in(tmp_path_factory: pytest.TempPathFactory) -> StandIn:
    return make_stand_in(tmp_path_factory.mktemp("stand-in"))


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory, home: Path) -> AsyncIterator[Runtime]:
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n\n[agent]\nlessons = "off"\n'
        "playbooks = false\n\n[memory]\nembed_enabled = false\n",
        encoding="utf-8",
    )
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def address(runtime: Runtime) -> AsyncIterator[str]:
    manager: RunManager = RunManager(runtime)
    stopping: asyncio.Event = asyncio.Event()
    listener = bind_listener("127.0.0.1", 0)
    port: int = listener.getsockname()[1]
    context: ServerContext = ServerContext(
        runtime=runtime,
        manager=manager,
        scheduler=None,
        token=TOKEN,
        host="127.0.0.1",
        port=port,
        started_at=time.time(),
        stopping=stopping,
    )
    server: DotServer = DotServer(serving_config(create_app(context)), stopping)
    serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
    while not server.started:
        assert not serving.done(), "the server stopped while starting"
        await asyncio.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        stopping.set()
        await serving
        await manager.shutdown()


@pytest_asyncio.fixture(loop_scope="module")
async def client(address: str) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        base_url=address, headers={"Authorization": f"Bearer {TOKEN}"}, timeout=_WAIT_S
    ) as signed_in:
        yield signed_in


@pytest.fixture
def mac_can(stand_in: StandIn, monkeypatch: pytest.MonkeyPatch) -> StandIn:
    """This Mac as one that can write down: the compiler the daemon builds with is the stand-in. The daemon of this
    module remembers a Mac without a compiler for a short while, and one with it for its life: the Mac that could not
    (the test before) is asked again at once, and the stand-in, once found, is the one every test after it finds."""
    monkeypatch.setenv("DOT_SWIFTC", str(stand_in.compiler))
    monkeypatch.setattr(helper, "MISSING_KEPT_S", 0.0)
    stand_in.says()
    return stand_in


def _kept(home: Path) -> list[str]:
    """What is left in the folder the daemon keeps a recording in while it is written down."""
    folder: Path = home / "voice" / "recordings"
    return sorted(entry.name for entry in folder.iterdir()) if folder.exists() else []


async def test_both_routes_are_behind_the_token_like_every_other(address: str, mac_can: StandIn) -> None:
    async with httpx.AsyncClient(base_url=address) as stranger:
        assert (await stranger.get("/api/voice")).status_code == 401
        refused: httpx.Response = await stranger.post("/api/voice/transcribe", json=_body(wav_of(SAMPLES)))
        assert refused.status_code == 401
    async with httpx.AsyncClient(base_url=address, headers={"Authorization": "Bearer not-the-token"}) as wrong:
        assert (await wrong.get("/api/voice")).status_code == 401
        assert (await wrong.post("/api/voice/transcribe", json=_body(wav_of(SAMPLES)))).status_code == 401
    assert mac_can.runs_made() == []


async def test_a_mac_that_cannot_says_why_and_writes_nothing_down(client: httpx.AsyncClient, stand_in: StandIn) -> None:
    """The tests run with a compiler that does not exist (``MISSING_SWIFTC``, tests/smoke/conftest.py). This runs before
    any test that gives the Mac a compiler: the daemon of the module remembers a compiler it found for its life."""
    answer: httpx.Response = await client.get("/api/voice")
    assert answer.status_code == 200
    assert answer.json() == {"transcribe": False, "why": NO_COMPILER}
    refused: httpx.Response = await client.post("/api/voice/transcribe", json=_body(wav_of(SAMPLES)))
    assert refused.status_code == 422
    assert refused.json()["context"]["code"] == "unavailable"
    assert "This Mac cannot write recordings down." in refused.json()["message"]
    assert stand_in.built() == 0 and stand_in.runs_made() == []


async def test_a_mac_that_can_says_so_and_builds_the_program_in_the_background(
    client: httpx.AsyncClient, mac_can: StandIn
) -> None:
    # The first test of this server that has the Mac able: nothing was built before the page asked.
    assert mac_can.built() == 0
    answer: httpx.Response = await client.get("/api/voice")
    assert answer.json() == {"transcribe": True, "why": None}
    # The page asked once as it started; the program is built by the time the first recording comes.
    for _ in range(int(_WAIT_S / 0.05)):
        if mac_can.built() == 1:
            break
        await asyncio.sleep(0.05)
    assert mac_can.built() == 1
    # Asked again, as a page loaded later does, it is not built again.
    await client.get("/api/voice")
    await asyncio.sleep(0.3)
    assert mac_can.built() == 1


async def test_a_recording_is_written_down_from_a_file_of_dots_own_making_that_is_gone_after(
    client: httpx.AsyncClient, mac_can: StandIn, home: Path
) -> None:
    mac_can.says(text="Merhaba bugün hava çok güzel")
    answer: httpx.Response = await client.post("/api/voice/transcribe", json=_body(wav_of(SAMPLES), "tr-TR"))
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"text": "Merhaba bugün hava çok güzel"}
    given: dict[str, Any] = mac_can.runs_made()[-1]
    assert given["argc"] == 3 and given["language"] == "tr-TR"
    # The standard header, the page's samples and the silence after them; a file of mode 0600.
    assert given["head"] == ["RIFF", "WAVEfmt ", "data"]
    assert given["format"] == [1, 1, RECORDING_RATE, RECORDING_RATE * 2, 2, 16]
    assert given["samples"] == len(SAMPLES) + _TAIL_SAMPLES
    assert given["peak"] == max(SAMPLES)
    assert given["mode"] == "0o600"
    assert _kept(home) == []
    # Another language, another time: the program is the one built, and the language is the one asked.
    again: httpx.Response = await client.post("/api/voice/transcribe", json=_body(wav_of(SAMPLES), "en-US"))
    assert again.status_code == 200
    assert mac_can.runs_made()[-1]["language"] == "en-US"
    assert mac_can.built() == 1


async def test_silence_is_an_empty_answer_and_not_an_error(client: httpx.AsyncClient, mac_can: StandIn) -> None:
    mac_can.says(text="")
    answer: httpx.Response = await client.post("/api/voice/transcribe", json=_body(wav_of(SAMPLES)))
    assert (answer.status_code, answer.json()) == (200, {"text": ""})


async def test_five_minutes_are_the_most_a_recording_may_be(client: httpx.AsyncClient, mac_can: StandIn) -> None:
    five: httpx.Response = await client.post("/api/voice/transcribe", json=_body(_silence(MAX_RECORDING_SECONDS)))
    assert five.status_code == 200, five.text
    runs: int = len(mac_can.runs_made())
    over: httpx.Response = await client.post("/api/voice/transcribe", json=_body(_silence(MAX_RECORDING_SECONDS + 1)))
    assert over.status_code == 422
    assert over.json()["message"] == "the recording is longer than five minutes"
    assert len(mac_can.runs_made()) == runs


async def test_a_body_past_the_limit_is_refused_and_is_not_said_back(
    client: httpx.AsyncClient, mac_can: StandIn
) -> None:
    runs: int = len(mac_can.runs_made())
    huge: str = "A" * (_RECORDING_B64_CHARS + 4)
    refused: httpx.Response = await client.post("/api/voice/transcribe", json={"language": "tr-TR", "data_b64": huge})
    assert refused.status_code == 422
    assert refused.json()["message"] == "the request is not valid"
    assert len(refused.text) < 2000 and "AAAAAAAA" not in refused.text
    assert len(mac_can.runs_made()) == runs


@pytest.mark.parametrize(
    ("data_b64", "message"),
    [
        ("this is not base64!!", "the recording is not valid base64"),
        (base64.b64encode(b"hello, not a recording").decode(), "the recording is not a WAV file"),
        (base64.b64encode(b"RIFF\0\0\0\0AVI LIST").decode(), "the recording is not a WAV file"),
        (base64.b64encode(wav_of([])).decode(), "the recording is empty"),
    ],
)
async def test_what_is_no_recording_is_refused_with_the_reason_and_nothing_is_started(
    client: httpx.AsyncClient, mac_can: StandIn, data_b64: str, message: str
) -> None:
    runs: int = len(mac_can.runs_made())
    refused: httpx.Response = await client.post(
        "/api/voice/transcribe", json={"language": "tr-TR", "data_b64": data_b64}
    )
    assert refused.status_code == 422
    assert refused.json()["message"] == message
    assert "Traceback" not in refused.text
    assert len(mac_can.runs_made()) == runs


async def test_a_recording_that_is_not_16_bit_mono_sound_is_refused(
    client: httpx.AsyncClient, mac_can: StandIn
) -> None:
    stereo: bytearray = bytearray(wav_of(SAMPLES))
    stereo[22:24] = (2).to_bytes(2, "little")
    refused: httpx.Response = await client.post("/api/voice/transcribe", json=_body(bytes(stereo)))
    assert refused.status_code == 422
    assert "not 16-bit mono sound" in refused.json()["message"]


@pytest.mark.parametrize("language", ["tr_TR", "../../etc", "tr-TR; id", "TR-tr", "english", "-h", "$(id)"])
async def test_a_language_that_is_no_tag_is_refused_and_nothing_is_started(
    client: httpx.AsyncClient, mac_can: StandIn, language: str
) -> None:
    runs: int = len(mac_can.runs_made())
    refused: httpx.Response = await client.post("/api/voice/transcribe", json=_body(wav_of(SAMPLES), language))
    assert refused.status_code == 422
    assert "not a language" in refused.json()["message"] or refused.json()["message"] == "the request is not valid"
    assert len(mac_can.runs_made()) == runs


@pytest.mark.parametrize(
    ("control", "code", "words"),
    [
        ({"mode": "error", "code": "unsupported-language"}, "unsupported-language", "choose another language"),
        ({"mode": "error", "code": "no-audio"}, "no-audio", "could not read the recording as sound"),
        ({"mode": "error", "code": "failed"}, "failed", "The Mac could not write the recording down"),
        ({"mode": "crash"}, "failed", "The Mac could not write the recording down"),
    ],
)
async def test_what_the_program_cannot_do_is_said_in_plain_words_and_leaves_no_recording(
    client: httpx.AsyncClient, mac_can: StandIn, home: Path, control: dict[str, Any], code: str, words: str
) -> None:
    mac_can.says(**control)
    answer: httpx.Response = await client.post("/api/voice/transcribe", json=_body(wav_of(SAMPLES), "xx-YY"))
    assert answer.status_code == 422
    body: dict[str, Any] = answer.json()
    assert set(body) == {"message", "context"} and body["context"]["code"] == code
    assert words in body["message"]
    assert "Traceback" not in answer.text and 'File "' not in answer.text
    assert _kept(home) == []
    # The route works after a failure: the next recording is written down.
    mac_can.says(text="fine again")
    assert (await client.post("/api/voice/transcribe", json=_body(wav_of(SAMPLES)))).json() == {"text": "fine again"}


async def test_pages_that_load_while_a_slow_mac_is_asked_share_one_question_and_no_other_route_waits(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The review of item 626: every page load asks `GET /api/voice`, which asked the Mac's tools (three programs, ten
    seconds each at the most) each time, and the page waited for the answer. The daemon asks once: the pages that load
    while the Mac is slow wait for that question, no other route does, and what the Mac said is remembered. A daemon of
    this test's own, for the one of the module has been asked before; the Mac is without a compiler (the tests'
    default), so that no program is built in the folder the other tests build in."""
    asked: list[int] = []
    real = helper.find_compiler

    async def slow() -> object:
        asked.append(1)
        await asyncio.sleep(0.5)
        return await real()

    monkeypatch.setattr(helper, "find_compiler", slow)
    manager: RunManager = RunManager(runtime)
    port: int = 18626
    context: ServerContext = ServerContext(
        runtime=runtime,
        manager=manager,
        scheduler=None,
        token=TOKEN,
        host="127.0.0.1",
        port=port,
        started_at=time.time(),
        stopping=asyncio.Event(),
    )
    transport = httpx.ASGITransport(app=create_app(context))
    async with httpx.AsyncClient(
        transport=transport, base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}
    ) as client:
        loads: list[asyncio.Task[httpx.Response]] = [asyncio.create_task(client.get("/api/voice")) for _ in range(6)]
        await asyncio.sleep(0.1)
        began: float = time.monotonic()
        health: httpx.Response = await client.get("/api/health")
        assert health.status_code == 200
        assert time.monotonic() - began < 0.3, "a route waited for the Mac to be asked"
        answers: list[httpx.Response] = await asyncio.gather(*loads)
        assert [answer.json() for answer in answers] == [{"transcribe": False, "why": NO_COMPILER}] * 6
        # The next loads are answered from what the Mac said.
        for _ in range(3):
            assert (await client.get("/api/voice")).json() == {"transcribe": False, "why": NO_COMPILER}
    assert len(asked) == 1
    await manager.shutdown()
