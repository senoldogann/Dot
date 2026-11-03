"""What the tests of the microphone's recording (items 626, 969) stand in for: the Swift compiler the daemon builds the
program that writes down what is said with, and that program. The compiler builds nothing: it copies a program of the
test's own to the place the daemon names. The program reads the sound streamed to it to the end, notes what it was
given, and answers as the test says, so a test can read what a page recorded and sent. Nothing here builds Swift or
touches Apple's frameworks."""

import json
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# What the stand-in program does with a stream: it reads its input to the end (16-bit samples, no header), notes what
# it was given (how many samples, the loudest, the crossings of a tone, the silence at the end) in `runs.jsonl`, then
# answers as `control.json` says. A language it has no dictation of is said before it reads, as the real one does.
_PROGRAM: str = """#!{python}
import json
import struct
import sys
import time
from pathlib import Path

control = json.loads(Path({control!r}).read_text(encoding="utf-8"))
mode = control.get("mode", "words")
if mode == "error" and control["code"] == "unsupported-language":
    print(json.dumps({{"error": control["code"], "detail": "the stand-in program says so"}}))
    sys.exit(1)
if mode == "slow":
    time.sleep(control.get("seconds", 60))
data = sys.stdin.buffer.read()
count = len(data) // 2
samples = struct.unpack_from("<" + str(count) + "h", data) if count > 0 else ()
tail = 0
for sample in reversed(samples):
    if sample != 0:
        break
    tail += 1
note = {{
    "argc": len(sys.argv),
    "language": sys.argv[1],
    "bytes": len(data),
    "samples": count,
    "peak": max((abs(sample) for sample in samples), default=0),
    "crossings": sum(1 for a, b in zip(samples, samples[1:]) if a < 0 <= b and max(abs(a), abs(b)) > 200),
    "tail": tail,
}}
with open({runs!r}, "a", encoding="utf-8") as log:
    log.write(json.dumps(note) + "\\n")
if mode == "words":
    print(json.dumps({{"text": control.get("text", "hello from the mac")}}))
elif mode == "error":
    print(json.dumps({{"error": control["code"], "detail": "the stand-in program says so"}}))
    sys.exit(1)
elif mode == "crash":
    sys.exit(139)
"""

# The compiler writes the program where `-o` says and notes that it was asked to build.
_COMPILER: str = """#!/bin/sh
echo "$@" >> "{builds}"
while [ $# -gt 0 ]; do
  if [ "$1" = "-o" ]; then out="$2"; fi
  shift
done
cp "{program}" "$out"
chmod 755 "$out"
"""


@dataclass(frozen=True)
class StandIn:
    """The stand-in compiler (``compiler``, what ``DOT_SWIFTC`` names) and program, and the notes they keep."""

    compiler: Path
    program: Path
    builds: Path
    runs: Path
    control: Path

    def says(self, **control: Any) -> None:
        """What the program answers from now on: ``text="..."`` for words, ``mode="error", code="..."`` for an error the
        program reports, ``mode="crash"`` and ``mode="slow"`` (it sleeps before it reads)."""
        self.control.write_text(json.dumps(control), encoding="utf-8")

    def built(self) -> int:
        """How often the compiler was asked to build the program."""
        return len(self.builds.read_text(encoding="utf-8").splitlines()) if self.builds.exists() else 0

    def runs_made(self) -> list[dict[str, Any]]:
        """What the program was given of each stream, in order."""
        if not self.runs.exists():
            return []
        return [json.loads(line) for line in self.runs.read_text(encoding="utf-8").splitlines()]


def make_stand_in(folder: Path) -> StandIn:
    """A stand-in compiler and program in ``folder``, answering ``hello from the mac`` until a test says otherwise."""
    stand: StandIn = StandIn(
        compiler=folder / "swiftc",
        program=folder / "stand-in-program",
        builds=folder / "builds.log",
        runs=folder / "runs.jsonl",
        control=folder / "control.json",
    )
    stand.says()
    stand.program.write_text(
        _PROGRAM.format(python=sys.executable, control=str(stand.control), runs=str(stand.runs)), encoding="utf-8"
    )
    stand.compiler.write_text(_COMPILER.format(builds=stand.builds, program=stand.program), encoding="utf-8")
    stand.compiler.chmod(0o755)
    return stand


def pcm_of(samples: list[int]) -> bytes:
    """``samples`` as the bytes the page streams: 16-bit little-endian, no header."""
    return struct.pack(f"<{len(samples)}h", *samples)
