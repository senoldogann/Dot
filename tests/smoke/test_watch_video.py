"""watch_video on a real video file (item 1212): ffmpeg makes the video, the tool runs as a run outside yolo runs it (in
the sandbox), and the Mac's dictation model is a stand-in program of the test's own (as in tests/unit/test_voice_helper:
no test builds Swift). It checks the frames and their moments, the transcript by the minute, the fence and the mark of
untrusted content, the refusal of a protected file, of Dot's own server, of a host on the local network and of a list
named as a video, and the call that names no language. The real dictation model and real links were tried by hand
(roadmap/1212.md)."""

import asyncio
import base64
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from dotagent.agent.gate import RunGate
from dotagent.agent.review import ReviewRequest, ReviewVerdict
from dotagent.agent.verify import new_facts
from dotagent.config import load_config
from dotagent.core.errors import PrivateHostRefused, ToolExecutionError, ToolInputError
from dotagent.core.paths import workspace_dir
from dotagent.core.types import JsonObject
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.store.models import RunRecord, ThreadRecord
from dotagent.tools.base import PreparedCall, ToolResult
from dotagent.tools.context import ToolContext, run_sandbox
from dotagent.tools.video import WATCH_VIDEO_TOOL
from dotagent.video import media

pytestmark = pytest.mark.asyncio(loop_scope="module")
# The checks that start ffmpeg need it; the refusals, which start nothing, run everywhere (the gate has no ffmpeg).
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is not on this Mac")

GOAL: str = "Watch the video."
_HEARD: str = "the stand-in heard this"

_COMPILER: str = """#!/bin/sh
while [ $# -gt 0 ]; do
  if [ "$1" = "-o" ]; then out="$2"; fi
  shift
done
cp "{program}" "$out"
chmod 755 "$out"
"""
_PROGRAM: str = """#!/bin/sh
cat > /dev/null
printf '{{"text":"{heard}"}}\\n'
"""


def _stand_in_compiler(folder: Path) -> Path:
    """A compiler that "builds" the stand-in program, which answers every piece of sound with the same words."""
    program: Path = folder / "stand-in-program"
    program.write_text(_PROGRAM.format(heard=_HEARD), encoding="utf-8")
    compiler: Path = folder / "swiftc"
    compiler.write_text(_COMPILER.format(program=program), encoding="utf-8")
    compiler.chmod(0o755)
    return compiler


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    home: Path = tmp_path_factory.mktemp("home")
    compiler: Path = _stand_in_compiler(tmp_path_factory.mktemp("swift"))
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    patch.setenv("DOT_SWIFTC", str(compiler))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


async def _nobody(request: ReviewRequest) -> ReviewVerdict:
    raise AssertionError(f"the review model must not be asked about this: {request.action.summary}")


async def _context(runtime: Runtime) -> ToolContext:
    thread: ThreadRecord = await runtime.store.create_thread("video", "test")
    run: RunRecord = await runtime.store.create_run(
        thread.id,
        GOAL,
        "task",
        "ollama:none",
        None,
        "test",
        "medium",
        "standard",
        None,
        "auto",
        False,
        None,
        None,
        None,
    )
    return ToolContext(
        runtime=runtime,
        browser=runtime.browser,
        run_id=run.id,
        agent_id=None,
        thread_id=thread.id,
        mode="task",
        approvals="auto",
        questions="leave",
        workspace=workspace_dir(),
        gate=RunGate(
            runtime.store,
            runtime.bus,
            runtime.approvals,
            run.id,
            "task",
            "aggressive",
            runtime.config.policy.pace,
            runtime.config.policy.spend,
            "auto",
            _nobody,
            (GOAL,),
            runtime.site_locks,
        ),
        cancel=asyncio.Event(),
        output_chars=runtime.config.agent.tool_output_chars,
        facts=new_facts,
    )


async def _watched(ctx: ToolContext, args: JsonObject) -> ToolResult:
    prepared: PreparedCall = await ctx.runtime.registry.tools[WATCH_VIDEO_TOOL].prepare(args, ctx)
    await ctx.gate.authorize(prepared.action)
    return await prepared.run(ctx)


def _video(path: Path, seconds: int, sound: bool) -> Path:
    """A video of ``seconds`` seconds of ffmpeg's test pattern, with a tone when ``sound``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = ["-f", "lavfi", "-i", f"testsrc2=size=320x240:rate=5:duration={seconds}"]
    if sound:
        inputs += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", *inputs, "-c:v", "libx264", "-pix_fmt", "yuv420p", "-shortest", str(path)],
        check=True,
    )
    return path


@needs_ffmpeg
async def test_a_file_is_seen_in_frames_and_heard_by_the_minute(runtime: Runtime) -> None:
    ctx: ToolContext = await _context(runtime)
    assert run_sandbox(ctx).search, "the test must run the programs in the sandbox"
    folder: Path = workspace_dir() / "videos"
    video: Path = await asyncio.to_thread(_video, folder / "talk.mp4", 130, True)
    result: ToolResult = await _watched(ctx, {"source": str(video), "frames": 4, "language": "en-US"})
    assert not result.is_error, result.text
    assert result.untrusted
    assert len(result.images) == 4
    for image in result.images:
        assert base64.b64decode(image.data_b64)[:2] == b"\xff\xd8", "each frame is a JPEG"
    assert "02:10 long; this look covers 00:00-02:10" in result.text
    assert "1) 00:16, 2) 00:48, 3) 01:21, 4) 01:53" in result.text
    assert re.search(r'<web_content nonce="[0-9a-f]+"', result.text) is not None
    assert f"[00:00] {_HEARD}\n[01:00] {_HEARD}\n[02:00] {_HEARD}" in result.text
    # A closer look: the part asked, and no more than a frame a second.
    closer: ToolResult = await _watched(ctx, {"source": str(video), "start": 60, "end": 63, "language": "en-US"})
    assert len(closer.images) == 3
    assert "this look covers 01:00-01:03" in closer.text
    assert f"[01:00] {_HEARD}" in closer.text and "[00:00]" not in closer.text
    # The transcript alone, of a video with no sound.
    silent: Path = await asyncio.to_thread(_video, folder / "silent.mp4", 5, False)
    quiet: ToolResult = await _watched(ctx, {"source": str(silent), "frames": 0})
    assert quiet.images == () and "The video has no sound." in quiet.text


@needs_ffmpeg
async def test_a_file_with_sound_needs_the_language_spoken(runtime: Runtime) -> None:
    ctx: ToolContext = await _context(runtime)
    folder: Path = workspace_dir() / "videos"
    video: Path = await asyncio.to_thread(_video, folder / "short.mp4", 3, True)
    with pytest.raises(ToolInputError, match="name the language spoken"):
        await _watched(ctx, {"source": str(video), "frames": 1})
    with pytest.raises(ToolInputError, match="past the end of the video"):
        await _watched(ctx, {"source": str(video), "start": 10, "language": "en-US"})


async def test_a_protected_file_and_dots_own_server_are_refused(runtime: Runtime) -> None:
    ctx: ToolContext = await _context(runtime)
    prepared: PreparedCall = await runtime.registry.tools[WATCH_VIDEO_TOOL].prepare(
        {"source": str(Path.home() / ".ssh" / "talk.mp4")}, ctx
    )
    assert prepared.action.kind == "read" and "secret_access" in prepared.action.flags
    # Dot's own server is no video to download, as it is no page for web_fetch.
    own: str = f"http://127.0.0.1:{runtime.config.server.port}/api/runs"
    with pytest.raises(ToolInputError, match="not an address Dot may open"):
        await runtime.registry.tools[WATCH_VIDEO_TOOL].prepare({"source": own}, ctx)
    # A host on the local network is refused before yt-dlp is started, as web_fetch refuses it.
    with pytest.raises(PrivateHostRefused):
        await _watched(ctx, {"source": "http://192.168.1.10/talk.mp4", "frames": 0})
    link: PreparedCall = await runtime.registry.tools[WATCH_VIDEO_TOOL].prepare(
        {"source": "https://www.youtube.com/watch?v=jNQXAC9IVRw"}, ctx
    )
    assert (link.action.kind, link.action.target) == ("network", "https://www.youtube.com/watch?v=jNQXAC9IVRw")


@needs_ffmpeg
async def test_a_list_named_as_a_video_is_not_followed(runtime: Runtime) -> None:
    ctx: ToolContext = await _context(runtime)
    listed: Path = await asyncio.to_thread(_video, workspace_dir() / "videos" / "listed.mp4", 2, False)
    disguised: Path = workspace_dir() / "videos" / "list.mp4"
    await asyncio.to_thread(disguised.write_text, f"ffconcat version 1.0\nfile {listed.name}\n", "utf-8")
    with pytest.raises(ToolExecutionError, match="not on whitelist"):
        await _watched(ctx, {"source": str(disguised), "frames": 1})


def _age(root: Path, at: float) -> None:
    for folder in root.iterdir():
        os.utime(folder, (at, at))


async def test_a_links_folder_is_held_by_one_look_and_swept_only_when_old_and_free(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    now: float = time.time()
    first_in: asyncio.Event = asyncio.Event()
    release: asyncio.Event = asyncio.Event()
    order: list[str] = []

    async def look(name: str) -> None:
        async with media.link_folder("https://example.org/v", now):
            order.append(f"{name} in")
            first_in.set()
            await release.wait()
            order.append(f"{name} out")

    first: asyncio.Task[None] = asyncio.create_task(look("first"))
    await first_in.wait()
    second: asyncio.Task[None] = asyncio.create_task(look("second"))
    await asyncio.sleep(0.2)
    # Held: an old folder of a link that a look holds is not swept, one that none holds is.
    async with media.link_folder("https://example.org/other", now):
        pass
    old: float = now - media.KEPT_S - 60
    root: Path = tmp_path / "dot-video"
    await asyncio.to_thread(_age, root, old)
    async with media.link_folder("https://example.org/third", now):
        pass
    release.set()
    await asyncio.gather(first, second)
    assert order == ["first in", "first out", "second in", "second out"]
    assert len(await asyncio.to_thread(os.listdir, root)) == 2, (
        "the held link's folder stays and the free old one is swept"
    )
