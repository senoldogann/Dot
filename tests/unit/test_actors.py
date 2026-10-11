"""The faces of the actors (item 83): the hash that picks an actor's hue and the ring of eight it picks from. The hash
is run in node from the module itself, so the Python mirror here proves the one the browser uses."""

import json
import shutil
import subprocess

import pytest

from dotagent.server.app import STATIC_DIR

_IDS: tuple[str, ...] = ("run-1", "run-2", "campaign-7f3a", "schedule-9", "", "Ünïcödé-id")


def _fnv1a(text: str) -> int:
    """FNV-1a over the UTF-16 code units, 32-bit, as actors.js computes it."""
    value: int = 0x811C9DC5
    units: bytes = text.encode("utf-16-le")
    for index in range(0, len(units), 2):
        value ^= int.from_bytes(units[index : index + 2], "little")
        value = (value * 0x01000193) & 0xFFFFFFFF
    return value


def test_the_hue_of_an_id_is_its_hash_on_the_ring_of_eight() -> None:
    node: str | None = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed: the browser's hash cannot be run here")
    script: str = (
        f"import {{ hueOf, hashId, HUES }} from '{(STATIC_DIR / 'js' / 'actors.js').as_uri()}';"
        f"console.log(JSON.stringify({{ hues: {json.dumps(_IDS)}.map(hueOf), hashes: {json.dumps(_IDS)}.map(hashId), ring: HUES }}));"
    )
    result = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    seen: dict[str, object] = json.loads(result.stdout)
    assert seen["ring"] == 8
    assert seen["hashes"] == [_fnv1a(text) for text in _IDS]
    assert seen["hues"] == [_fnv1a(text) % 8 for text in _IDS]


def test_every_hue_of_the_ring_has_a_token_and_a_rule() -> None:
    themes: str = (STATIC_DIR / "css" / "themes.css").read_text(encoding="utf-8")
    app: str = (STATIC_DIR / "css" / "app.css").read_text(encoding="utf-8")
    for hue in range(8):
        assert f"--hue-{hue}: light-dark(" in themes, hue
        assert f'.avatar[data-hue="{hue}"] .face' in app, hue
