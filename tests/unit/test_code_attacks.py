"""The threat model of code mode (item 1500): what the code of a ``code_run`` is held to, attack by attack.

Each test is an input that gets through with the verdict it gets, and its twin's. Before any of it, the attack is made
with no sandbox (the control, :meth:`Box.unguarded`): an attack the control cannot make proves nothing about the
sandbox, so each test asserts that it works there first. Then the same code runs in the sandbox, through the real
runner, bridge and interpreter, and is refused as a kernel refuses it; and its twin, the harmless thing next to it that
the code needs, works. These run on macOS alone: the sandbox is the kernel's.
"""

import asyncio
import contextlib
import http.server
import os
import pwd
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from code_mode_stand_ins import Box, box

from dotagent.codemode import runner, sandbox
from dotagent.codemode.runner import Ended
from dotagent.core.paths import dot_home

pytestmark = pytest.mark.macos

DECOY: str = "DECOY-SECRET-OF-THE-OWNER"
REFUSED: str = "Operation not permitted"


@pytest.fixture
def code_box(tmp_path: Path) -> Box:
    return box(tmp_path)


def _real_home() -> Path:
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


@contextlib.contextmanager
def _listening() -> Iterator[tuple[int, int]]:
    """A TCP listener and a web server of this Mac, the ports of which an attack is given: the control reaches them."""
    listener: socket.socket = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()

    class Hello(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"hello from the web server")

        def log_message(self, *_arguments: object) -> None:
            return None

    web: http.server.HTTPServer = http.server.HTTPServer(("127.0.0.1", 0), Hello)
    thread: threading.Thread = threading.Thread(target=web.serve_forever, daemon=True)
    thread.start()
    try:
        yield listener.getsockname()[1], web.server_address[1]
    finally:
        web.shutdown()
        web.server_close()
        listener.close()


def test_a_file_outside_the_scratch_folder_is_not_read_and_one_inside_is(code_box: Box, tmp_path: Path) -> None:
    owners: Path = tmp_path / "owners-file.txt"
    owners.write_text(DECOY, encoding="utf-8")
    attack: str = f"print(open({str(owners)!r}).read())"
    assert DECOY in code_box.unguarded(attack)
    ended: Ended = code_box.start(attack)
    assert (ended.ending, REFUSED in ended.output, DECOY in ended.output) == ("failed", True, False)
    twin: Ended = code_box.start("open('mine.txt', 'w').write('mine'); print(open('mine.txt').read())")
    assert (twin.ending, twin.output.strip()) == ("finished", "mine")


def test_nothing_is_written_outside_the_scratch_folder_and_the_tools_are_read_only(
    code_box: Box, tmp_path: Path
) -> None:
    outside: Path = tmp_path / "planted-outside.txt"
    attack: str = f"open({str(outside)!r}, 'w').write('planted')"
    code_box.unguarded(attack)
    assert outside.read_text(encoding="utf-8") == "planted"
    outside.unlink()
    ended: Ended = code_box.start(attack)
    assert (ended.ending, REFUSED in ended.output, outside.exists()) == ("failed", True, False)
    # The tools folder, where the launcher and ``dot_tools`` are, is the code's to read and never to change.
    bridge: Path = code_box.layout.tools / "dot_tools" / "_bridge.py"
    changing: Ended = code_box.start(f"open({str(bridge)!r}, 'a').write('x = 1')")
    assert (changing.ending, REFUSED in changing.output) == ("failed", True)
    assert "x = 1" not in bridge.read_text(encoding="utf-8")
    twin: Ended = code_box.start(f"import os\nprint(len(os.listdir({str(code_box.layout.tools)!r})) > 0)")
    assert (twin.ending, twin.output.strip()) == ("finished", "True")


def test_the_network_is_closed_to_a_socket_and_to_urllib_and_a_local_pair_works(code_box: Box) -> None:
    with _listening() as (tcp_port, web_port):
        attack: str = (
            "import socket, urllib.request\n"
            f"socket.create_connection(('127.0.0.1', {tcp_port}), timeout=3).close()\n"
            "print('socket connected')\n"
            f"print(urllib.request.urlopen('http://127.0.0.1:{web_port}', timeout=3).read().decode())\n"
        )
        before: str = code_box.unguarded(attack)
        assert "socket connected" in before and "hello from the web server" in before
        for part in (
            f"import socket; socket.create_connection(('127.0.0.1', {tcp_port}), timeout=3)",
            f"import urllib.request; urllib.request.urlopen('http://127.0.0.1:{web_port}', timeout=3)",
            "import socket; s = socket.socket(); s.bind(('127.0.0.1', 0))",
            "import socket; socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(b'x', ('127.0.0.1', 9))",
            "import socket; socket.gethostbyname('example.com')",
        ):
            ended: Ended = code_box.start(part)
            assert ended.ending == "failed", part
            assert "hello from the web server" not in ended.output
    twin: Ended = code_box.start(
        "import socket, urllib.parse\n"
        "a, b = socket.socketpair(); a.send(b'x'); print(b.recv(1), urllib.parse.urlsplit('http://x.y/z').path)"
    )
    assert (twin.ending, twin.output.strip()) == ("finished", "b'x' /z")


def test_no_program_can_be_started_by_subprocess_system_fork_or_spawn(code_box: Box) -> None:
    marker: Path = code_box.layout.scratch / "ran"
    forking: str = (
        f"import os\nif os.fork() == 0:\n    open({str(marker)!r}, 'w').write('child')\n    os._exit(0)\n"
        "import time\ntime.sleep(0.5)"
    )
    attacks: dict[str, str] = {
        "subprocess": f"import subprocess; subprocess.run(['/usr/bin/touch', {str(marker)!r}], check=True)",
        "os.system": f"import os; os.system('/usr/bin/touch {marker}')",
        "os.posix_spawn": f"import os; os.posix_spawn('/usr/bin/touch', ['touch', {str(marker)!r}], {{}})",
        "os.execv": f"import os; os.execv('/usr/bin/touch', ['touch', {str(marker)!r}])",
        "os.fork": forking,
    }
    for name, attack in attacks.items():
        code_box.layout.scratch.mkdir(parents=True, exist_ok=True)
        marker.unlink(missing_ok=True)
        code_box.unguarded(attack)
        time.sleep(0.3)
        assert marker.exists(), f"{name} runs a program with no sandbox"
        marker.unlink()
        ended: Ended = code_box.start(attack)
        assert not marker.exists(), f"{name} started a program in the sandbox ({ended.output})"
    twin: Ended = code_box.start(
        "import threading\nout = []\n"
        "threads = [threading.Thread(target=out.append, args=(i,)) for i in range(20)]\n"
        "[t.start() for t in threads]; [t.join() for t in threads]\nprint(len(out))"
    )
    assert (twin.ending, twin.output.strip()) == ("finished", "20")


# ``import ctypes`` asks ``os.uname()`` for the kernel's release, which the sandbox refuses (the name of the Mac comes
# with it), so a code that wants ctypes has to say what the check wants first: the attacks below do, to reach the calls
# themselves, which the profile refuses whatever way a code gets to them.
CTYPES: str = "import os\nos.uname = lambda: type('U', (), {'release': '25.0.0'})()\nimport ctypes\n"


def test_ctypes_reaches_neither_a_program_nor_a_file_the_profile_closes(code_box: Box, tmp_path: Path) -> None:
    owners: Path = tmp_path / "owners-file.txt"
    owners.write_text(DECOY, encoding="utf-8")
    marker: Path = code_box.layout.scratch / "by-ctypes"
    attack: str = (
        f"{CTYPES}libc = ctypes.CDLL(None)\n"
        f"print('system', libc.system(b'/usr/bin/touch {marker}'))\n"
        f"print('open', libc.open({str(owners).encode()!r}, 0))\n"
    )
    code_box.layout.scratch.mkdir(parents=True, exist_ok=True)
    before: str = code_box.unguarded(attack)
    assert "system 0" in before and marker.exists()
    assert int(before.split("open ")[1].split()[0]) >= 0
    marker.unlink()
    ended: Ended = code_box.start(attack)
    assert (ended.ending, marker.exists(), "system 0" in ended.output) == ("finished", False, False)
    assert "open -1" in ended.output
    # Without that, ``import ctypes`` fails at once: the sandbox is the second thing in its way, not the first.
    plain: Ended = code_box.start("import ctypes")
    assert plain.ending == "failed" and "uname" in plain.output
    twin: Ended = code_box.start(
        f"{CTYPES}print(ctypes.c_int(5).value, ctypes.CDLL(None).getpid() > 0, ctypes.sizeof(ctypes.c_long))"
    )
    assert (twin.ending, twin.output.strip()) == ("finished", "5 True 8")


def test_dots_home_and_the_library_folder_and_the_keychain_are_closed(code_box: Box) -> None:
    home: Path = dot_home()
    home.mkdir(parents=True, exist_ok=True)
    key: Path = home / ".env"
    key.write_text(f"OPENAI_API_KEY={DECOY}\n", encoding="utf-8")
    attack: str = f"print(open({str(key)!r}).read())"
    assert DECOY in code_box.unguarded(attack)
    ended: Ended = code_box.start(attack)
    assert (ended.ending, REFUSED in ended.output, DECOY in ended.output) == ("failed", True, False)
    library: Path = _real_home() / "Library"
    for place in (library, library / "Keychains", library / "Application Support", Path("/Users"), Path("/etc")):
        listed: Ended = code_box.start(f"import os; print(os.listdir({str(place)!r})[:3])")
        assert listed.ending == "failed", f"{place} can be listed: {listed.output}"
        assert REFUSED in listed.output
    twin: Ended = code_box.start(
        f"import os, json\nprint(os.path.isfile(json.__file__), len(os.listdir({str(code_box.layout.tools)!r})) > 0)"
    )
    assert (twin.ending, twin.output.strip()) == ("finished", "True True")


def test_the_environment_of_the_code_holds_no_secret_and_keeps_the_time_zone(
    code_box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    secrets: dict[str, str] = {
        "GITHUB_TOKEN": "ghp_decoy0123456789abcdef",
        "OPENAI_API_KEY": "sk-decoy-0123456789",
        "AWS_SECRET_ACCESS_KEY": "decoy/aws/0123456789",
        "MY_PASSWORD": "hunter2-decoy",
        "DATABASE_URL": "postgres://app:decoy-password@db.example/app",
        "SESSION_COOKIE": "decoy-cookie",
    }
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("TZ", "Europe/Helsinki")
    attack: str = "import os; print(sorted(os.environ))"
    # The control hands the code the daemon's own environment, as a program started with it would have it.
    before: str = code_box.unguarded(
        "import os; print(sorted(os.environ.items()))", env={name: os.environ[name] for name in secrets}
    )
    assert all(value in before for value in secrets.values())
    ended: Ended = code_box.start(attack)
    assert ended.ending == "finished"
    assert not any(value in ended.output or name in ended.output for name, value in secrets.items())
    names: set[str] = set(ended.output.strip("[]\n").replace("'", "").split(", "))
    # What the Mac says of its language may come too (``LC_CTYPE``, ``LC_ALL``); nothing else of the daemon's does.
    assert names - {"LC_CTYPE", "LC_ALL"} == {"HOME", "LANG", "TMPDIR", "TZ"}
    twin: Ended = code_box.start("import os; print(os.environ['TZ'], os.environ['HOME'] == os.getcwd())")
    assert (twin.ending, twin.output.strip()) == ("finished", "Europe/Helsinki True")


def test_an_infinite_loop_is_killed_at_its_own_time_and_a_slow_call_is_not_held_against_it(code_box: Box) -> None:
    spin: str = "while True:\n    pass"
    # The control does not end by itself: it is cut by the timeout of the test's own subprocess.
    assert code_box.unguarded(spin, timeout_s=1.0) == "never ended"
    for loop in (spin, "import time\nwhile True:\n    time.sleep(0.05)"):
        ended: Ended = code_box.start(loop, seconds=1.0)
        assert ended.ending == "timeout"
        assert 1.0 <= ended.own_seconds < 3.0
        assert ended.signal == 9
    # The time a call takes is the tool's own and the owner's, with limits of their own: it is not the code's.
    code_box.says("slow", "{}", after_s=2.0)
    twin: Ended = code_box.start("from dot_tools._bridge import call\ncall('slow', {})\nprint('waited')", seconds=1.0)
    assert (twin.ending, twin.output.strip()) == ("finished", "waited")
    assert twin.seconds >= 2.0 and twin.own_seconds < 1.0


def test_a_memory_bomb_is_killed_when_it_holds_more_than_its_limit(code_box: Box) -> None:
    bomb: str = "held = []\nwhile True:\n    held.append(b'x' * 20_000_000)"
    ended: Ended = code_box.start(bomb, memory_mb=150)
    assert (ended.ending, ended.signal) == ("memory", 9)
    assert 150 <= ended.peak_mb < 1500
    # The control shows the bomb is one: with no limit it holds this much, and the twin, a smaller hold, is let be.
    assert "209715200" in code_box.unguarded("x = b'x' * 209_715_200; print(len(x))")
    twin: Ended = code_box.start("x = b'x' * 30_000_000\nprint(len(x))", memory_mb=150)
    assert (twin.ending, twin.output.strip()) == ("finished", "30000000")


def test_a_huge_print_is_kept_as_its_head_and_its_tail_and_an_endless_one_is_stopped(
    code_box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    flood: str = "for i in range(60000):\n    print('line', i, 'y' * 80)"
    assert code_box.unguarded(flood).count("\n") == 60000
    ended: Ended = code_box.start(flood, window_bytes=2000)
    assert ended.ending == "finished"
    assert ended.printed_bytes > 5_000_000
    assert len(ended.output) < 5000
    assert "bytes of output omitted" in ended.output
    assert ended.output.startswith("line 0 ") and "line 59999 " in ended.output
    twin: Ended = code_box.start("print('a few words')", window_bytes=2000)
    assert (twin.ending, twin.output.strip(), twin.printed_bytes) == ("finished", "a few words", 12)
    monkeypatch.setattr(runner, "MAX_OUTPUT_BYTES", 1_000_000)
    endless: Ended = code_box.start("while True:\n    print('y' * 1000)", window_bytes=2000, seconds=30.0)
    assert endless.ending == "output"
    assert endless.seconds < 20.0


def test_a_fork_bomb_fails_at_its_first_fork_and_the_code_holds_one_process(code_box: Box) -> None:
    forked: str = "import os\npid = os.fork()\nif pid == 0:\n    os._exit(0)\nprint('forked')"
    assert "forked" in code_box.unguarded(forked)
    ended: Ended = code_box.start("import os\nwhile True:\n    os.fork()")
    assert ended.ending == "failed"
    assert "Error" in ended.output and "fork" in ended.output
    limit: Ended = code_box.start("import resource\nprint(resource.getrlimit(resource.RLIMIT_NPROC))")
    assert (limit.ending, limit.output.strip()) == ("finished", "(1, 1)")
    # The other limits the kernel holds the process to: the largest file it writes, the files it has open, no core.
    reading: str = (
        "import resource\nfor name in ('CPU', 'FSIZE', 'NOFILE', 'CORE'):\n"
        "    print(name, resource.getrlimit(getattr(resource, 'RLIMIT_' + name)))"
    )
    soft: dict[str, str] = dict(line.split(" ", 1) for line in code_box.start(reading).output.splitlines())
    assert soft["CORE"] == "(0, 0)"
    assert soft["FSIZE"] == f"({runner.FILE_MAX_BYTES}, {runner.FILE_MAX_BYTES})"
    assert soft["NOFILE"] == f"({runner.OPEN_FILES_MAX}, {runner.OPEN_FILES_MAX})"
    assert soft["CPU"] == "(25, 27)"
    twin: Ended = code_box.start("print(sum(range(1000)))")
    assert (twin.ending, twin.output.strip()) == ("finished", "499500")


def test_a_code_that_fills_its_scratch_folder_is_stopped_and_a_file_past_its_size_is_refused(
    code_box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner, "SCRATCH_MAX_ENTRIES", 50)
    monkeypatch.setattr(runner, "_SCRATCH_EVERY_S", 0.1)
    filler: str = (
        "import time\nfor i in range(100000):\n    open(f'f{i}', 'w').write('x')\n"
        "    if i % 20 == 0:\n        time.sleep(0.05)"
    )
    ended: Ended = code_box.start(filler, seconds=30.0)
    assert ended.ending == "scratch"
    assert runner.clear_scratch_if_full(code_box.layout)
    assert list(code_box.layout.scratch.iterdir()) == []
    # A write is cut at the size the kernel allows (what it wrote is that size), and the next one is refused.
    big: Ended = code_box.start(
        "import os\nfd = os.open('big', os.O_WRONLY | os.O_CREAT)\n"
        f"first = os.write(fd, b'x' * {runner.FILE_MAX_BYTES + 1000})\n"
        "try:\n    os.write(fd, b'x')\nexcept OSError as error:\n"
        f"    print(first == {runner.FILE_MAX_BYTES}, 'refused', error.errno)"
    )
    assert (big.ending, big.output.strip()) == ("finished", "True refused 27")
    twin: Ended = code_box.start("open('small', 'w').write('x'); print('kept')")
    assert (twin.ending, twin.output.strip()) == ("finished", "kept")


async def _until_there(path: Path) -> int:
    """The pid the code wrote at ``path``, once it has."""
    for _ in range(150):
        await asyncio.sleep(0.1)
        if await asyncio.to_thread(path.exists):
            text: str = await asyncio.to_thread(path.read_text, "utf-8")
            if text:
                return int(text)
    raise AssertionError(f"{path} never came")


async def test_the_runs_stop_kills_the_code_and_so_does_a_cancelled_task(code_box: Box) -> None:
    sleeper: str = (
        "import os, time\nopen('pid', 'w').write(str(os.getpid()))\nprint('started', flush=True)\ntime.sleep(120)"
    )
    stop: asyncio.Event = asyncio.Event()
    running: asyncio.Task[Ended] = asyncio.create_task(code_box.run(sleeper, cancel=stop))
    pid: int = await _until_there(code_box.layout.scratch / "pid")
    stop.set()
    ended: Ended = await asyncio.wait_for(running, timeout=10.0)
    assert (ended.ending, ended.signal, "started" in ended.output) == ("stopped", 9, True)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    # The cancellation of the step that runs it (the run's Stop reaches a tool as one) leaves nothing running either.
    await asyncio.to_thread((code_box.layout.scratch / "pid").unlink)
    task: asyncio.Task[Ended] = asyncio.create_task(code_box.run(sleeper))
    again: int = await _until_there(code_box.layout.scratch / "pid")
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ProcessLookupError):
        os.kill(again, 0)


def test_nothing_runs_when_the_sandbox_cannot_start(code_box: Box, monkeypatch: pytest.MonkeyPatch) -> None:
    marker: Path = code_box.layout.scratch / "ran"
    code: str = f"open({str(marker)!r}, 'w').write('the code ran')"
    code_box.layout.scratch.mkdir(parents=True, exist_ok=True)
    # With the sandbox, the code runs: this is the twin of what follows.
    assert code_box.start(code).ending == "finished" and marker.exists()
    marker.unlink()
    monkeypatch.setattr(sandbox, "SANDBOX_EXEC", "/nonexistent/sandbox-exec")
    ended: Ended = code_box.start(code)
    assert ended.ending == "unstartable"
    assert not marker.exists()
    sandbox.forget_probes()
    probe = asyncio.run(sandbox.code_mode_ready(code_box.interpreter))
    assert (probe.ok, "not there" in probe.why) == (False, True)
    sandbox.forget_probes()


def test_a_profile_the_kernel_refuses_or_one_that_does_not_hold_is_no_sandbox(
    code_box: Box, monkeypatch: pytest.MonkeyPatch
) -> None:
    marker: Path = code_box.layout.scratch / "ran"
    code_box.layout.scratch.mkdir(parents=True, exist_ok=True)
    code: str = f"open({str(marker)!r}, 'w').write('the code ran')"
    with monkeypatch.context() as broken:
        broken.setattr(sandbox, "sandbox_profile", lambda *_arguments: "(version 1) (this is not a profile")
        ended: Ended = code_box.start(code)
        assert ended.ending == "failed" and not marker.exists()
        refused = sandbox.probe_blocking(code_box.interpreter)
        assert not refused.ok and "did not start the interpreter" in refused.why
    with monkeypatch.context() as open_profile:
        open_profile.setattr(sandbox, "sandbox_profile", lambda *_arguments: "(version 1)\n(allow default)")
        holds = sandbox.probe_blocking(code_box.interpreter)
        assert not holds.ok
        assert "does not hold" in holds.why and "tools-write: worked" in holds.why and "network" in holds.why
    # The profile as it is, is the twin: it holds, and the probe says so.
    assert sandbox.probe_blocking(code_box.interpreter).ok
