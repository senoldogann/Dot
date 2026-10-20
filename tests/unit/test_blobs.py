"""``strip_blobs``: a picture's base64 in a call's arguments goes by its size, a long command stays readable."""

import base64

from dotagent.core.text import strip_blobs

_LIMIT: int = 4000


def test_a_blob_goes_by_its_size_and_a_long_command_stays() -> None:
    picture: str = base64.b64encode(bytes(range(256)) * 20).decode()
    command: str = "echo " + "word " * 120
    assert len(command) > 400
    shown = strip_blobs({"image": picture, "command": command, "list": [command]}, _LIMIT)
    assert shown == {"image": f"<{len(picture)} chars>", "command": command, "list": [command]}


def test_a_text_past_the_limit_is_cut_in_the_middle_and_not_hidden() -> None:
    command: str = "echo " + "word " * 2000
    shown = strip_blobs({"command": command}, _LIMIT)
    assert isinstance(shown, dict)
    text = shown["command"]
    assert isinstance(text, str)
    assert len(text) <= _LIMIT
    assert text.startswith("echo word")
    assert "chars omitted" in text


def test_without_a_limit_every_long_string_goes_as_before() -> None:
    assert strip_blobs("word " * 200) == "<1000 chars>"
