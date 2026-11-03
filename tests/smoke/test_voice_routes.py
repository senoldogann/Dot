"""The routes of the microphone's recording (items 626, 969) on a real server: whether this Mac can write down what is
said, and a stream of it written down as it comes, with a compiler and a program of the test's own in the place of
Apple's (``voice_stand_ins``).

A page records the voice itself and streams it here in numbered pieces while the owner speaks; the Mac writes it down
on the device. These tests read the routes as a page does: behind the token like every other, a Mac that cannot says
why and starts nothing, a piece is read before anything is done with it (its size, base64 of 16-bit samples, its turn,
a language that is no tag), the program is given the samples and the silence after them and no file holds them, the
cross drops a stream, and what fails says what to do, never a stack."""

import asyncio
import base64
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import pytest_asyncio
from voice_stand_ins import StandIn, make_stand_in, pcm_of

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.views import _PIECE_B64_CHARS
from dotagent.voice import helper
from dotagent.voice.toolchain import NO_COMPILER
from dotagent.voice.wav import RECORDING_RATE, TAIL_SECONDS

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "voice-routes-token"
_TAIL_SAMPLES: int = round(TAIL_SECONDS * RECORDING_RATE)
_WAIT_S: float = 30.0
# A recording of a second of a tone, as the page writes it.
SAMPLES: list[int] = [round(9000 * ((index // 20) % 2 * 2 - 1)) for index in range(RECORDING_RATE)]


# A piece as a page sends one: a quarter of a second.
_PIECE_BYTES: int = RECORDING_RATE // 2


def _piece(number: int, pcm: bytes) -> dict[str, int | str]:
    return {"piece": number, "data_b64": base64.b64encode(pcm).decode("ascii")}


async def _open(client: httpx.AsyncClient, language: str = "tr-TR") -> str:
    opened: httpx.Response = await client.post("/api/voice/streams", json={"language": language})
    assert opened.status_code == 200, opened.text
    name: str = opened.json()["id"]
    return name


async def _write_down(client: httpx.AsyncClient, pcm: bytes, language: str = "tr-TR") -> httpx.Response:
    """What a page does from the press to the tick: a stream opened, ``pcm`` sent in pieces, and the stream ended."""
    name: str = await _open(client, language)
    for number, at in enumerate(range(0, len(pcm), _PIECE_BYTES)):
        fed: httpx.Response = await client.post(
            f"/api/voice/streams/{name}/audio", json=_piece(number, pcm[at : at + _PIECE_BYTES])
        )
        if fed.status_code != 204:
            return fed
    return await client.post(f"/api/voice/streams/{name}/end")


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
    """What the voice folder holds but the folders the program was built in: no sound is ever written there."""
    folder: Path = home / "voice"
    return (
        sorted(entry.name for entry in folder.iterdir() if not entry.name.startswith("build-"))
        if folder.exists()
        else []
    )


async def test_the_routes_are_behind_the_token_like_every_other(address: str, mac_can: StandIn) -> None:
    allow: dict[str, str] = {"pane": "microphone"}
    piece: dict[str, int | str] = _piece(0, pcm_of(SAMPLES[:100]))
    for headers in ({}, {"Authorization": "Bearer not-the-token"}):
        async with httpx.AsyncClient(base_url=address, headers=headers) as stranger:
            assert (await stranger.get("/api/voice")).status_code == 401
            assert (await stranger.post("/api/voice/streams", json={"language": "tr-TR"})).status_code == 401
            assert (await stranger.post("/api/voice/streams/any/audio", json=piece)).status_code == 401
            assert (await stranger.post("/api/voice/streams/any/end")).status_code == 401
            assert (await stranger.delete("/api/voice/streams/any")).status_code == 401
            # Item 636: nothing is read or shown for a caller without the token.
            assert (await stranger.post("/api/voice/allow", json=allow)).status_code == 401
    assert mac_can.runs_made() == []


async def test_system_settings_is_asked_for_by_one_of_two_panes_and_nothing_else(client: httpx.AsyncClient) -> None:
    """Item 636: the daemon's own application has the route that shows System Settings at the pane where Dot.app is
    allowed the microphone, and a body it does not know is refused before anything is read or shown. The answers of a
    Dot.app that runs are the unit tests' (`tests/unit/test_voice_desktop_app.py`): here the daemon would ask this
    Mac."""
    for body in ({"pane": "camera"}, {"pane": ""}, {}, {"pane": "microphone", "url": "https://example.com"}):
        refused: httpx.Response = await client.post("/api/voice/allow", json=body)
        assert refused.status_code == 422, (body, refused.text)
        assert refused.json()["message"] == "the request is not valid"


async def test_a_mac_that_cannot_says_why_and_writes_nothing_down(client: httpx.AsyncClient, stand_in: StandIn) -> None:
    """The tests run with a compiler that does not exist (``MISSING_SWIFTC``, tests/smoke/conftest.py). This runs before
    any test that gives the Mac a compiler: the daemon of the module remembers a compiler it found for its life."""
    answer: httpx.Response = await client.get("/api/voice")
    assert answer.status_code == 200
    assert answer.json() == {"transcribe": False, "why": NO_COMPILER}
    refused: httpx.Response = await client.post("/api/voice/streams", json={"language": "tr-TR"})
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


async def test_a_stream_is_given_every_piece_and_the_silence_after_them_and_no_file_holds_them(
    client: httpx.AsyncClient, mac_can: StandIn, home: Path
) -> None:
    mac_can.says(text="Merhaba bugün hava çok güzel")
    answer: httpx.Response = await _write_down(client, pcm_of(SAMPLES), "tr-TR")
    assert answer.status_code == 200, answer.text
    assert answer.json() == {"text": "Merhaba bugün hava çok güzel"}
    given: dict[str, Any] = mac_can.runs_made()[-1]
    assert given["argc"] == 2 and given["language"] == "tr-TR"
    # The page's samples, in their order (the tone's every crossing is there, the last into the silence), and the
    # silence after them.
    assert given["samples"] == len(SAMPLES) + _TAIL_SAMPLES
    assert given["peak"] == max(SAMPLES)
    assert given["crossings"] == len(SAMPLES) // 40
    assert given["tail"] == _TAIL_SAMPLES
    assert _kept(home) == []
    # Another language, another time: the program is the one built, and the language is the one asked.
    again: httpx.Response = await _write_down(client, pcm_of(SAMPLES), "en-US")
    assert again.status_code == 200
    assert mac_can.runs_made()[-1]["language"] == "en-US"
    assert mac_can.built() == 1


async def test_silence_is_an_empty_answer_and_not_an_error(client: httpx.AsyncClient, mac_can: StandIn) -> None:
    mac_can.says(text="")
    answer: httpx.Response = await _write_down(client, pcm_of(SAMPLES))
    assert (answer.status_code, answer.json()) == (200, {"text": ""})


async def test_the_cross_drops_a_stream_and_a_stream_that_is_no_longer_open_says_so(
    client: httpx.AsyncClient, mac_can: StandIn
) -> None:
    runs: int = len(mac_can.runs_made())
    name: str = await _open(client)
    assert (await client.post(f"/api/voice/streams/{name}/audio", json=_piece(0, pcm_of(SAMPLES)))).status_code == 204
    assert (await client.delete(f"/api/voice/streams/{name}")).status_code == 204
    assert (await client.delete(f"/api/voice/streams/{name}")).status_code == 204
    gone: httpx.Response = await client.post(f"/api/voice/streams/{name}/end")
    assert gone.status_code == 404
    assert "no longer open" in gone.json()["message"]
    # The program was killed before its input ended: it wrote nothing down.
    assert len(mac_can.runs_made()) == runs


async def test_five_minutes_are_the_most_a_stream_may_be(
    client: httpx.AsyncClient, mac_can: StandIn, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Five minutes are 9.6 MB of samples: the limit is brought down to a second for the test, in the daemon of this
    # process.
    monkeypatch.setattr(helper, "_MOST_STREAM_BYTES", RECORDING_RATE * 2)
    name: str = await _open(client)
    second: bytes = pcm_of(SAMPLES)
    assert (await client.post(f"/api/voice/streams/{name}/audio", json=_piece(0, second))).status_code == 204
    over: httpx.Response = await client.post(f"/api/voice/streams/{name}/audio", json=_piece(1, second[:2]))
    assert over.status_code == 422
    assert over.json()["context"]["code"] == "too-long"
    assert (await client.post(f"/api/voice/streams/{name}/end")).status_code == 404


async def test_a_piece_past_its_size_is_refused_and_is_not_said_back(
    client: httpx.AsyncClient, mac_can: StandIn
) -> None:
    name: str = await _open(client)
    huge: str = "A" * (_PIECE_B64_CHARS + 4)
    refused: httpx.Response = await client.post(f"/api/voice/streams/{name}/audio", json={"piece": 0, "data_b64": huge})
    assert refused.status_code == 422
    assert refused.json()["message"] == "the request is not valid"
    assert len(refused.text) < 2000 and "AAAAAAAA" not in refused.text
    await client.delete(f"/api/voice/streams/{name}")


@pytest.mark.parametrize(
    ("piece", "message"),
    [
        ({"piece": 0, "data_b64": "this is not base64!!"}, "the sound is not valid base64"),
        ({"piece": 0, "data_b64": base64.b64encode(b"odd").decode()}, "the sound is not 16-bit samples"),
        ({"piece": 1, "data_b64": base64.b64encode(bytes(64)).decode()}, "a piece of the recording was lost"),
        ({"piece": -1, "data_b64": base64.b64encode(bytes(64)).decode()}, "the request is not valid"),
    ],
)
async def test_what_is_no_piece_in_its_turn_is_refused_with_the_reason(
    client: httpx.AsyncClient, mac_can: StandIn, piece: dict[str, int | str], message: str
) -> None:
    name: str = await _open(client)
    refused: httpx.Response = await client.post(f"/api/voice/streams/{name}/audio", json=piece)
    assert refused.status_code == 422
    assert refused.json()["message"].startswith(message)
    assert "Traceback" not in refused.text
    await client.delete(f"/api/voice/streams/{name}")


@pytest.mark.parametrize("language", ["tr_TR", "../../etc", "tr-TR; id", "TR-tr", "english", "-h", "$(id)"])
async def test_a_language_that_is_no_tag_is_refused_and_nothing_is_started(
    client: httpx.AsyncClient, mac_can: StandIn, language: str
) -> None:
    runs: int = len(mac_can.runs_made())
    refused: httpx.Response = await client.post("/api/voice/streams", json={"language": language})
    assert refused.status_code == 422
    assert "not a language" in refused.json()["message"] or refused.json()["message"] == "the request is not valid"
    assert len(mac_can.runs_made()) == runs


@pytest.mark.parametrize(
    ("control", "code", "words"),
    [
        ({"mode": "error", "code": "unsupported-language"}, "unsupported-language", "choose another language"),
        ({"mode": "error", "code": "failed"}, "failed", "The Mac could not write down what you said"),
        ({"mode": "crash"}, "failed", "The Mac could not write down what you said"),
    ],
)
async def test_what_the_program_cannot_do_is_said_in_plain_words_and_leaves_nothing_behind(
    client: httpx.AsyncClient, mac_can: StandIn, home: Path, control: dict[str, Any], code: str, words: str
) -> None:
    mac_can.says(**control)
    answer: httpx.Response = await _write_down(client, pcm_of(SAMPLES), "xx-YY")
    assert answer.status_code == 422
    body: dict[str, Any] = answer.json()
    assert set(body) == {"message", "context"} and body["context"]["code"] == code
    assert words in body["message"]
    assert "Traceback" not in answer.text and 'File "' not in answer.text
    assert _kept(home) == []
    # The routes work after a failure: the next stream is written down.
    mac_can.says(text="fine again")
    assert (await _write_down(client, pcm_of(SAMPLES))).json() == {"text": "fine again"}


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
    # The Mac is asked, and answers only once the test lets it: a route that waited for it would never answer.
    answer_now: asyncio.Event = asyncio.Event()

    async def slow() -> object:
        asked.append(1)
        await answer_now.wait()
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
        # Answered while the Mac has not answered yet: no route waits for it (a wall clock would fail under load).
        health: httpx.Response = await asyncio.wait_for(client.get("/api/health"), 10)
        assert health.status_code == 200
        assert not answer_now.is_set() and asked == [1], "a route waited for the Mac to be asked"
        answer_now.set()
        answers: list[httpx.Response] = await asyncio.gather(*loads)
        assert [answer.json() for answer in answers] == [{"transcribe": False, "why": NO_COMPILER}] * 6
        # The next loads are answered from what the Mac said.
        for _ in range(3):
            assert (await client.get("/api/voice")).json() == {"transcribe": False, "why": NO_COMPILER}
    assert len(asked) == 1
    await manager.shutdown()
