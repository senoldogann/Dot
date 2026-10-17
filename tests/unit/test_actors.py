"""The faces of the actors (items 83 and 102): the hash that picks an actor's hue, the ring of eight it picks from, and
the look each hue gives; the look of a chat with Dot (item 277). The hash is run in node from the module itself, so the
Python mirror here proves the one the browser uses."""

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
        f"import {{ hueOf, hashId, HUES }} from '{(STATIC_DIR / 'js' / 'hues.js').as_uri()}';"
        f"console.log(JSON.stringify({{ hues: {json.dumps(_IDS)}.map(hueOf), hashes: {json.dumps(_IDS)}.map(hashId), "
        f"ring: HUES }}));"
    )
    result = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    seen: dict[str, object] = json.loads(result.stdout)
    assert seen["ring"] == 8
    assert seen["hashes"] == [_fnv1a(text) for text in _IDS]
    assert seen["hues"] == [_fnv1a(text) % 8 for text in _IDS]


def test_every_hue_of_the_ring_gives_a_look_of_its_own_from_the_catalogue() -> None:
    node: str | None = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed: the looks cannot be read here")
    script: str = (
        f"import {{ hueLook, HUES }} from '{(STATIC_DIR / 'js' / 'hues.js').as_uri()}';"
        f"import {{ SHAPE_IDS, COLOUR_IDS }} from '{(STATIC_DIR / 'js' / 'character' / 'skins.js').as_uri()}';"
        "console.log(JSON.stringify({ looks: Array.from({ length: HUES }, (_, hue) => hueLook(hue)), shapes: SHAPE_IDS,"
        " colours: COLOUR_IDS }));"
    )
    result = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    payload = json.loads(result.stdout)
    looks: list[dict[str, str]] = payload["looks"]
    shapes: list[str] = payload["shapes"]
    colours: list[str] = payload["colours"]
    assert len(looks) == 8
    assert len({(look["shape"], look["colour"]) for look in looks}) == 8
    for look in looks:
        assert look["shape"] in shapes and look["colour"] in colours and look["expression"] == "neutral"


def test_a_chat_with_dot_wears_a_shape_and_a_colour_its_hash_picks_apart() -> None:
    """A chat with Dot (item 277): its shape is its hash on the ring, its colour the hash's next places on the ring, so
    two chats of one hue can still differ; sixty-four chats wear more than the ring's eight looks."""
    node: str | None = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed: the looks cannot be read here")
    ids: list[str] = [f"thr_{index}" for index in range(64)]
    script: str = (
        f"import {{ chatLook, HUE_COLOURS }} from '{(STATIC_DIR / 'js' / 'hues.js').as_uri()}';"
        f"import {{ SHAPE_IDS }} from '{(STATIC_DIR / 'js' / 'character' / 'skins.js').as_uri()}';"
        f"console.log(JSON.stringify({{ looks: {json.dumps(ids)}.map(chatLook), shapes: SHAPE_IDS, colours: "
        f"HUE_COLOURS }}));"
    )
    result = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, check=True)
    payload = json.loads(result.stdout)
    shapes: list[str] = payload["shapes"]
    colours: list[str] = payload["colours"]
    expected: list[dict[str, str]] = [
        {"shape": shapes[_fnv1a(text) % 8], "expression": "neutral", "colour": colours[_fnv1a(text) // 8 % 8]}
        for text in ids
    ]
    assert payload["looks"] == expected
    assert len({(look["shape"], look["colour"]) for look in expected}) > 8
