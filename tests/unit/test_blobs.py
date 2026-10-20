"""``strip_blobs_keep_text``: a picture's base64 in a call's arguments goes by its size, a long command stays readable,
and a key in it is masked before it is cut; ``strip_blobs`` replaces every long string by its size, as logs want."""

import base64

from dotagent.agent.executor import _safe_json
from dotagent.core.text import strip_blobs, strip_blobs_keep_text, truncate_middle

_LIMIT: int = 4000


def test_a_blob_goes_by_its_size_and_a_long_command_stays() -> None:
    picture: str = base64.b64encode(bytes(range(256)) * 20).decode()
    command: str = "echo " + "word " * 120
    assert len(command) > 400
    shown = strip_blobs_keep_text({"image": picture, "command": command, "list": [command]}, _LIMIT)
    assert shown == {"image": f"<{len(picture)} chars>", "command": command, "list": [command]}


def test_a_text_past_the_limit_is_cut_in_the_middle_and_not_hidden() -> None:
    command: str = "echo " + "word " * 2000
    shown = strip_blobs_keep_text({"command": command}, _LIMIT)
    assert isinstance(shown, dict)
    text = shown["command"]
    assert isinstance(text, str)
    assert len(text) <= _LIMIT
    assert text.startswith("echo word")
    assert "chars omitted" in text


def test_every_long_string_goes_by_its_size_in_a_log() -> None:
    assert strip_blobs("word " * 200) == "<1000 chars>"


def test_a_key_where_a_long_command_is_cut_is_masked_before_the_cut() -> None:
    key: str = "ghp_" + "a1B2c3D4e5" * 4
    # A command whose middle cut falls inside the key: cut first, a part of it would stay that no pattern knows.
    command: str = "echo " + "word " * 516 + f' && curl -H "X-Key: {key}" ' + "&& echo done " * 177
    cut_first: str = truncate_middle(command, _LIMIT)
    assert key not in cut_first and "a1B2c3D4e5" in cut_first
    shown = _safe_json({"command": command})
    text = shown["command"]
    assert isinstance(text, str) and "chars omitted" in text
    assert "a1B2c3D4e5" not in text
