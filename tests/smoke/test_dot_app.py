"""Dot.app (item 288), built for real in a throw-away home: clang builds its program and codesign signs it ad hoc
under Dot's bundle identifier, and built again from the same source it is left as it is, so that what macOS granted it
holds. Started the way launchd starts a job, responsible for itself, its program makes macOS hold Dot.app responsible
for the Python it starts: the program macOS's questions and System Settings name. It hands SIGTERM on to its child and
ends as its child ended. Nothing of this Mac's launchd, privacy settings or System Settings is touched.
"""

import os
import plistlib
import signal
import subprocess
import sys
from pathlib import Path

from dotagent.dot_app import BUNDLE_ID, app_path, ensure_app

XCRUN: str = "/usr/bin/xcrun"
CODESIGN: str = "/usr/bin/codesign"
# A stand-in for launchd: it starts its arguments with the attribute launchd starts a job with, which makes the job
# responsible for itself instead of for the program that started it.
DISCLAIMING_START: str = """
#include <spawn.h>
#include <stdio.h>
#include <sys/wait.h>
extern char **environ;
int responsibility_spawnattrs_setdisclaim(posix_spawnattr_t *attributes, int disclaim);
int main(int argc, char *argv[]) {
  posix_spawnattr_t attributes;
  posix_spawnattr_init(&attributes);
  responsibility_spawnattrs_setdisclaim(&attributes, 1);
  pid_t pid;
  if (argc < 2 || posix_spawn(&pid, argv[1], NULL, &attributes, &argv[1], environ) != 0) {
    perror("start");
    return 1;
  }
  int status;
  waitpid(pid, &status, 0);
  return WIFEXITED(status) ? WEXITSTATUS(status) : 1;
}
"""
WAIT_S: float = 30.0


def test_dot_app_is_built_once_and_macos_holds_it_responsible_for_the_daemon(tmp_path: Path) -> None:
    program: Path = ensure_app(tmp_path)
    app: Path = app_path(tmp_path)
    assert program == app / "Contents" / "MacOS" / "Dot"
    info: dict[str, object] = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert (info["CFBundleName"], info["CFBundleIdentifier"], info["CFBundleExecutable"]) == ("Dot", BUNDLE_ID, "Dot")
    signed: subprocess.CompletedProcess[str] = subprocess.run(
        [CODESIGN, "--display", "--verbose", str(app)], capture_output=True, text=True, check=False
    )
    assert signed.returncode == 0, signed.stderr
    assert f"Identifier={BUNDLE_ID}" in signed.stderr and "Signature=adhoc" in signed.stderr, signed.stderr
    verified: subprocess.CompletedProcess[str] = subprocess.run(
        [CODESIGN, "--verify", "--strict", str(app)], capture_output=True, text=True, check=False
    )
    assert verified.returncode == 0, verified.stderr

    # Built again from the same source, the app is the one macOS knows.
    built: os.stat_result = program.stat()
    assert ensure_app(tmp_path) == program
    assert (program.stat().st_ino, program.stat().st_mtime_ns) == (built.st_ino, built.st_mtime_ns)

    # Started as launchd starts it, the Python it starts reads Dot.app as the program macOS holds responsible.
    source: Path = tmp_path / "start.c"
    source.write_text(DISCLAIMING_START, encoding="utf-8")
    start: Path = tmp_path / "start"
    compiled: subprocess.CompletedProcess[str] = subprocess.run(
        [XCRUN, "clang", "-o", str(start), str(source)], capture_output=True, text=True, check=False
    )
    assert compiled.returncode == 0, compiled.stderr
    asked: subprocess.CompletedProcess[str] = subprocess.run(
        [
            str(start),
            str(program),
            sys.executable,
            "-c",
            "from dotagent.desktop.perms import program_name, responsible_program\n"
            "print(responsible_program()); print(program_name(responsible_program()))",
        ],
        capture_output=True,
        text=True,
        timeout=WAIT_S,
        check=False,
    )
    assert asked.returncode == 0, asked.stderr
    responsible, name = asked.stdout.splitlines()
    assert Path(responsible).resolve() == app.resolve(), asked.stdout
    assert name == "Dot"

    # It ends as its child ended, and hands SIGTERM on to it. The child sleeps in short steps in the foreground: the
    # shell runs its trap after each, while its `wait` for a sleep in the background (bash 3.2, macOS's /bin/sh) misses a
    # signal that comes just before it starts to wait, and so ran into WAIT_S on CI (measured through this launcher: 1
    # run in 500 did not end within 2 s; with the short steps none did, the slowest in 135 ms).
    assert subprocess.run([str(program), "/bin/sh", "-c", "exit 3"], check=False).returncode == 3
    waiting: subprocess.Popen[str] = subprocess.Popen(
        [str(program), "/bin/sh", "-c", 'trap "exit 7" TERM; echo ready; while :; do sleep 0.1; done'],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert waiting.stdout is not None
        assert waiting.stdout.readline() == "ready\n"
        waiting.send_signal(signal.SIGTERM)
        assert waiting.wait(WAIT_S) == 7
    finally:
        waiting.kill()
        waiting.wait(WAIT_S)
