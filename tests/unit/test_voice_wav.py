"""The sound the page streams to the Mac (items 626, 969) is what the daemon takes: the page's wav.js and
voicestream.js are run in node, and their samples, their rate, their five minutes and their largest piece are the
daemon's own."""

import base64
import json
import shutil
import struct
import subprocess

import pytest

from dotagent.server.app import STATIC_DIR
from dotagent.voice.wav import MAX_PIECE_BYTES, MAX_RECORDING_SECONDS, RECORDING_RATE


def test_the_pages_samples_rate_five_minutes_and_largest_piece_are_the_daemons() -> None:
    node: str | None = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed: the page's sound cannot be made here")
    wav: str = (STATIC_DIR / "js" / "wav.js").as_uri()
    stream: str = (STATIC_DIR / "js" / "voicestream.js").as_uri()
    script: str = (
        f"import {{ pcmOf, base64Of, RECORDING_RATE, MAX_RECORDING_S }} from '{wav}';"
        f"import {{ MOST_PIECE_SAMPLES }} from '{stream}';"
        "const samples = Int16Array.from([0, 1, -1, 32767, -32768, 1234, -4321]);"
        "console.log(JSON.stringify({ pcm: base64Of(pcmOf(samples)), rate: RECORDING_RATE, seconds: MAX_RECORDING_S,"
        " piece: MOST_PIECE_SAMPLES }));"
    )
    done = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    made: dict[str, object] = json.loads(done.stdout)
    assert isinstance(made["pcm"], str)
    assert struct.unpack("<7h", base64.b64decode(made["pcm"])) == (0, 1, -1, 32767, -32768, 1234, -4321)
    assert made["rate"] == RECORDING_RATE
    assert made["seconds"] == MAX_RECORDING_SECONDS
    assert isinstance(made["piece"], int) and made["piece"] * 2 <= MAX_PIECE_BYTES
