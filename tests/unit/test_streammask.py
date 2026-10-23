"""The text a step streams has its secrets masked as the message it stores has (item 551): one split between two deltas
is found whole, and no delta shows the start of one that the next completes."""

from collections.abc import Sequence
from typing import Literal

import pytest

from dotagent.agent.streammask import StreamMask
from dotagent.core.text import redact_known
from dotagent.llm.types import StreamDelta

# Made-up values in the forms of a key and of a secret Dot holds; none of them is a real one.
HELD: str = "correct-horse-battery-staple"
KEY_FORM: str = "sk-test-0123456789abcdefghij"
TOKEN: str = "Bearer Zm9vYmFyYmF6cXV4MTIzNDU2Nzg5MA"
PEM: str = (
    "-----BEGIN RSA PRIVATE KEY-----\nMIIEowIBAAKCAQEA1234567890\nabcdefghijklmnopqrstuvwxyzABCDEF\n"
    "-----END RSA PRIVATE KEY-----"
)
PROSE: str = " and the rest of the answer goes on for a while, with plain words in it, so that it streams. "


class _Screen:
    """What a screen is shown: the deltas the mask passes on, in order."""

    def __init__(self) -> None:
        self.deltas: list[StreamDelta] = []

    async def show(self, delta: StreamDelta) -> None:
        self.deltas.append(delta)

    def text(self, kind: Literal["text", "thinking"] = "text") -> str:
        return "".join(delta.text for delta in self.deltas if delta.kind == kind)


async def _streamed(text: str, sizes: Sequence[int], known: Sequence[str] = (HELD,)) -> tuple[str, str]:
    """``text`` streamed in pieces of ``sizes`` (one after the other, again and again); what was shown before the
    stream ended, and what was shown in all."""
    screen: _Screen = _Screen()
    mask: StreamMask = StreamMask(screen.show)
    mask.begin(known)
    start: int = 0
    index: int = 0
    while start < len(text):
        size: int = sizes[index % len(sizes)]
        await mask(StreamDelta(kind="text", text=text[start : start + size]))
        start += size
        index += 1
    before: str = screen.text()
    await mask.flush()
    return before, screen.text()


@pytest.mark.parametrize("sizes", [(1,), (2, 3), (5, 9), (1, 40), (7,)])
@pytest.mark.parametrize(
    "secret",
    [HELD, KEY_FORM, TOKEN, f"https://me:{KEY_FORM}@host.example/x", f"password={HELD}", f"Cookie: a={HELD}; b=2"],
    ids=["held", "key-form", "bearer", "url-password", "password-field", "cookie"],
)
async def test_a_secret_is_masked_whole_wherever_the_stream_splits_it(secret: str, sizes: tuple[int, ...]) -> None:
    text: str = f"Here is what the file says: {secret} and that is all.{PROSE * 2}"
    before, shown = await _streamed(text, sizes)
    assert shown == redact_known(text, (HELD,))
    assert HELD not in shown and KEY_FORM not in shown and "Zm9vYmFy" not in shown
    # Nothing of it was shown before the end either: what came before the end is a start of what was shown after.
    assert shown.startswith(before)
    assert "***" in before or secret[:10] not in before, before


@pytest.mark.parametrize("sizes", [(1,), (4,), (3, 11)])
async def test_a_private_key_is_shown_once_its_end_has_come_not_in_parts(sizes: tuple[int, ...]) -> None:
    text: str = f"The key file:\n{PEM}\nThat is all.{PROSE * 2}"
    before, shown = await _streamed(text, sizes, ())
    assert shown == redact_known(text, ())
    assert "MIIEow" not in shown and "abcdefghijklmnop" not in shown
    assert "MIIEow" not in before and "BEGIN" not in before.replace("-----BEGIN RSA PRIVATE KEY-----", "")


async def test_a_secret_longer_than_the_usual_wait_is_still_found_whole() -> None:
    long_one: str = "x9" * 80
    text: str = f"The long key is {long_one} and nothing else.{PROSE * 3}"
    before, shown = await _streamed(text, (3,), (long_one,))
    assert shown == redact_known(text, (long_one,)) and long_one[:20] not in shown
    assert long_one[:20] not in before


async def test_plain_text_is_shown_as_it_streams_all_but_the_word_it_is_writing() -> None:
    text: str = ("A plain answer with nothing secret in it, in plain words, one after the other. " * 4).rstrip()
    before, shown = await _streamed(text, (5,))
    assert shown == text
    # Only the word being written waits for what comes after it, so the end of the stream adds that word alone and
    # the message does not grow by lines as it ends.
    assert before == text[: text.rfind(" ") + 1]


async def test_a_short_reply_is_shown_when_the_stream_ends() -> None:
    before, shown = await _streamed("Done.", (1,))
    assert (before, shown) == ("", "Done.")


async def test_a_language_written_without_spaces_streams_too() -> None:
    text: str = "これは秘密のないとても長い答えです。" * 12
    before, shown = await _streamed(text, (4,))
    assert shown == text and len(before) > len(text) // 2


async def test_thinking_and_text_are_masked_each_and_the_one_is_shown_when_the_other_starts() -> None:
    screen: _Screen = _Screen()
    mask: StreamMask = StreamMask(screen.show)
    mask.begin((HELD,))
    for piece in ("I should read the file, ", f"it says {HELD[:9]}", f"{HELD[9:]} and that is a key."):
        await mask(StreamDelta(kind="thinking", text=piece))
    # The text starts: what the thought left waiting is shown first, masked.
    await mask(StreamDelta(kind="text", text="The key is "))
    await mask(StreamDelta(kind="text", text=f"{HELD}."))
    await mask.flush()
    assert screen.text("thinking") == "I should read the file, it says *** and that is a key."
    assert screen.text("text") == "The key is ***."
    assert [delta.kind for delta in screen.deltas][-1] == "text" and HELD not in str(screen.deltas)


async def test_a_reset_drops_what_waits_and_is_passed_on_and_a_new_step_starts_clean() -> None:
    screen: _Screen = _Screen()
    mask: StreamMask = StreamMask(screen.show)
    mask.begin((HELD,))
    await mask(StreamDelta(kind="text", text="The first of a reply that is asked again"))
    await mask(StreamDelta(kind="reset", text=""))
    await mask.flush()
    # The word that waited is dropped with the reply: the reset is the last the screen is shown.
    assert [delta.kind for delta in screen.deltas] == ["text", "reset"]
    assert screen.text() == "The first of a reply that is asked "
    await mask(StreamDelta(kind="text", text="half a reply that the next step does not finish"))
    mask.begin(())
    await mask.flush()
    # What the last step left waiting is not shown in the next one.
    assert screen.deltas[-1].text == "half a reply that the next step does not "


async def test_what_a_step_holds_is_masked_in_what_ends_the_stream_too() -> None:
    screen: _Screen = _Screen()
    mask: StreamMask = StreamMask(screen.show)
    mask.begin((HELD,))
    await mask(StreamDelta(kind="text", text=f"key {HELD}"))
    assert HELD[:9] not in screen.text()
    await mask.flush()
    assert screen.text() == "key ***"


@pytest.mark.parametrize("sizes", [(1,), (2, 3), (5,)])
@pytest.mark.parametrize(
    "introduction",
    ["the password = ", "Authorization: Basic ", "run it with --token ", "the token : "],
    ids=["password-spaced", "basic", "option", "token-spaced"],
)
async def test_words_that_introduce_a_secret_wait_for_its_value_however_they_come(
    introduction: str, sizes: tuple[int, ...]
) -> None:
    # The name and its separator come in pieces of their own (``password``, then `` = ``): the name waits for what may
    # follow it, so the value that comes after is masked with it and not shown alone.
    value: str = "Hunter2ZebraQuartz77yy"
    text: str = f"Here it is: {introduction}{value}{PROSE * 2}"
    before, shown = await _streamed(text, sizes, ())
    assert shown == redact_known(text, ())
    assert value[:6] not in before and value[:6] not in shown
