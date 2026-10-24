"""What the tests of the microphone's recording (item 626) stand in for: the Swift compiler the daemon builds the
program that writes a recording down with, and that program. The compiler builds nothing: it copies a program of the
test's own to the place the daemon names. The program answers as the test says and notes what it was given of the
recording, so a test can read what a page recorded and sent. Nothing here builds Swift or touches Apple's frameworks."""

import json
import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# What the stand-in program does with a recording: it notes the form of the file it was given (its header and its
# samples, the loudest of them, its mode) in `runs.jsonl`, then answers as `control.json` says.
_PROGRAM: str = """#!{python}
import json
import struct
import sys
import time
from pathlib import Path

control = json.loads(Path({control!r}).read_text(encoding="utf-8"))
wav = Path(sys.argv[1])
data = wav.read_bytes()
count = (len(data) - 44) // 2
samples = struct.unpack_from("<" + str(count) + "h", data, 44) if count > 0 else ()
note = {{
    "argc": len(sys.argv),
    "language": sys.argv[2],
    "bytes": len(data),
    "head": [data[0:4].decode("latin-1"), data[8:16].decode("latin-1"), data[36:40].decode("latin-1")],
    "format": list(struct.unpack_from("<HHIIHH", data, 20)),
    "samples": count,
    "peak": max((abs(sample) for sample in samples), default=0),
    "crossings": sum(1 for a, b in zip(samples, samples[1:]) if a < 0 <= b and max(abs(a), abs(b)) > 200),
    "mode": oct(wav.stat().st_mode & 0o777),
}}
with open({runs!r}, "a", encoding="utf-8") as log:
    log.write(json.dumps(note) + "\\n")
mode = control.get("mode", "words")
if mode == "words":
    print(json.dumps({{"text": control.get("text", "hello from the mac")}}))
elif mode == "error":
    print(json.dumps({{"error": control["code"], "detail": "the stand-in program says so"}}))
    sys.exit(1)
elif mode == "crash":
    sys.exit(139)
elif mode == "slow":
    time.sleep(control.get("seconds", 60))
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
        program reports, ``mode="crash"`` and ``mode="slow"``."""
        self.control.write_text(json.dumps(control), encoding="utf-8")

    def built(self) -> int:
        """How often the compiler was asked to build the program."""
        return len(self.builds.read_text(encoding="utf-8").splitlines()) if self.builds.exists() else 0

    def runs_made(self) -> list[dict[str, Any]]:
        """What the program was given of each recording, in order."""
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


def wav_of(samples: list[int], rate: int = 16_000) -> bytes:
    """A 16-bit mono WAV file of ``samples``, as the page writes one."""
    pcm: bytes = struct.pack(f"<{len(samples)}h", *samples)
    header: bytes = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + len(pcm),
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        rate,
        rate * 2,
        2,
        16,
        b"data",
        len(pcm),
    )
    return header + pcm
