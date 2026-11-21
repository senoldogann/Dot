"""A secret Dot holds is masked in every form a tool, a page or a log writes it back in (item 1292): as it is,
JSON-escaped, URL-encoded (``quote`` and ``quote_plus``), form-encoded, HTML-escaped and as base64 (the three
alignments of the standard and the URL-safe alphabet, padded and not), by the one function every mask of a held value
goes through."""

import base64
import html
import json
from collections.abc import Callable
from urllib.parse import quote, quote_plus

import pytest

from dotagent import secrets
from dotagent.core.secret_forms import _ordered, forms_of, held_forms
from dotagent.core.text import KNOWN_SECRET_MIN_CHARS, mask_known, redact_json, redact_known
from dotagent.secrets import mask_filled
from dotagent.server.diagnostics import tail_lines
from dotagent.vault.masking import MASK, masked

# A value with the characters each encoding writes differently (a quote, a plus, a slash, an equals sign, a backslash,
# an exclamation mark, a letter beyond ASCII); made up.
SECRET: str = 'p"a+s/w=rd\\x!é9'
LONG_OUTPUT_CHARS: int = 2 * 1024 * 1024


def _base64_whole(value: str) -> dict[str, str]:
    data: bytes = value.encode("utf-8")
    forms: dict[str, str] = {}
    for alphabet, encode in (("standard", base64.b64encode), ("urlsafe", base64.urlsafe_b64encode)):
        padded: str = encode(data).decode("ascii")
        forms[f"base64 {alphabet} padded"] = padded
        forms[f"base64 {alphabet} unpadded"] = padded.rstrip("=")
    return forms


_FORMS: dict[str, str] = {
    "plain": SECRET,
    "json": json.dumps(SECRET)[1:-1],
    "json twice": json.dumps(json.dumps(SECRET)[1:-1])[1:-1],
    "json unescaped": json.dumps(SECRET, ensure_ascii=False)[1:-1],
    "url quote": quote(SECRET),
    "url quote safe empty": quote(SECRET, safe=""),
    "url quote_plus": quote_plus(SECRET),
    "html": html.escape(SECRET),
    "html no quotes": html.escape(SECRET, quote=False),
    **_base64_whole(SECRET),
}


@pytest.mark.parametrize("name", sorted(_FORMS))
def test_a_held_secret_is_masked_in_each_form_in_a_tool_result(name: str) -> None:
    """The text a tool returns is masked by ``mask_known`` before the run reads it (agent/executor.py)."""
    form: str = _FORMS[name]
    shown: str = mask_known(f"result: [{form}] end", (SECRET,))
    assert shown == "result: [***] end", (name, shown)


@pytest.mark.parametrize("name", sorted(_FORMS))
def test_a_held_secret_is_masked_in_each_form_in_an_event_a_log_line_and_a_note(
    name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    form: str = _FORMS[name]
    # An event's data, as the executor masks it.
    assert redact_json({"text": f"saw {form}", "list": [form]}, (SECRET,)) == {"text": "saw ***", "list": ["***"]}
    assert redact_known(f"saw {form}", (SECRET,)) == "saw ***"
    # A line of a log, in the tail of it that a screen shows.
    line: bytes = f'{{"event": "request", "detail": "sent {form}"}}'.encode()
    shown_line: tuple[str, ...] = tail_lines(b"started\n" + line + b"\nended\n", False, 3, (SECRET,))
    assert shown_line == ("started", '{"event": "request", "detail": "sent ***"}', "ended")
    # A note a run saves, and a text a login of the vault filled.
    monkeypatch.setattr(secrets, "_filled", frozenset({SECRET}))
    assert mask_filled(f"saw {form}") == "saw ***"
    assert masked(f"saw {form}", (SECRET,)) == f"saw {MASK}"


@pytest.mark.parametrize("before", [0, 1, 2])
@pytest.mark.parametrize("encode", [base64.b64encode, base64.urlsafe_b64encode], ids=["standard", "urlsafe"])
def test_a_secret_inside_longer_base64_text_is_masked_whichever_bytes_stand_before_it(
    before: int, encode: Callable[[bytes], bytes]
) -> None:
    secret: str = "Tr0ub4dor&3-horse-staple"
    data: bytes = secret.encode()
    blob: str = encode(b"x" * before + data + b"-and-the-rest-of-it").decode("ascii")
    # The characters that the secret's bytes alone decide.
    alone: str = blob[(0, 2, 3)[before] : (before + len(data)) * 4 // 3]
    text: str = f"Authorization: Basic {blob} done"
    assert alone in text
    shown: str = mask_known(text, (secret,))
    assert alone not in shown and shown.startswith("Authorization: Basic ") and shown.endswith(" done"), shown


def test_a_line_a_logger_wrote_as_json_has_the_secret_masked(monkeypatch: pytest.MonkeyPatch) -> None:
    written: bytes = json.dumps({"event": "request", "detail": f"sent {SECRET}", "extra": json.dumps(SECRET)}).encode()
    shown: tuple[str, ...] = tail_lines(written + b"\n", False, 1, (SECRET,))
    assert shown == ('{"event": "request", "detail": "sent ***", "extra": "\\"***\\""}',)


def test_a_text_without_a_secret_is_unchanged_and_so_is_one_that_holds_only_short_values() -> None:
    prose: str = 'The build passed in 12 seconds; see https://example.com/a?b=c%20d&e=f+g and {"k": "v"} &amp; more.'
    assert mask_known(prose, (SECRET, "a-longer-secret-123")) == prose
    assert redact_known("nothing here", (SECRET,)) == "nothing here"
    short: str = "k" * (KNOWN_SECRET_MIN_CHARS - 1)
    assert mask_known(f"{short} and {quote_plus(short)}", (short,)) == f"{short} and {quote_plus(short)}"
    assert held_forms((short, ""), KNOWN_SECRET_MIN_CHARS) == ()


def test_a_value_from_the_minimum_length_on_is_masked_and_the_vault_keeps_its_shorter_minimum() -> None:
    eight: str = "abcd+ef/"
    assert len(eight) == KNOWN_SECRET_MIN_CHARS
    assert mask_known(f"x {eight} y {quote(eight, safe='')} z", (eight,)) == "x *** y *** z"
    # A one-time code of six digits is masked in the vault's pages as before; its base64 only from eight characters on,
    # since a shorter run of a picture's base64 would stand by chance.
    assert masked("code 123456 now", ("123456",)) == f"code {MASK} now"
    assert "123456" in held_forms(("123456",), 4)
    assert all(len(form) >= 8 for form in held_forms(("123456",), 4) if form != "123456")


def test_the_longest_form_is_masked_first_so_a_secret_inside_another_goes_with_it() -> None:
    held: str = "MARKER-a1b2c3d4e5"
    longer: str = f"{held}f6g7"
    assert mask_known(f"{held} then {longer}", (held, longer)) == "*** then ***"


def test_a_two_megabyte_output_is_masked_with_one_search_for_each_form_and_no_work_to_make_them() -> None:
    """The cost of a long output is one ``replace`` per form of the held set, so the promise is kept by counting: the
    forms of the set are made once (the caches take no new miss on the second call) and are few. No clock, which a
    loaded machine would fail."""
    values: tuple[str, ...] = (SECRET, "second-secret-value-1", "third/secret=value+2", "4th-secret-v4lue-xyz")
    line: str = "build step ok: compiled 12 files, 0 warnings, see https://example.com/a?b=c%20d&e=f+g\n"
    text: str = line * (LONG_OUTPUT_CHARS // len(line))
    planted: str = f"{text}{quote_plus(SECRET)}\n{text}"
    # The first call makes the forms of the set; the runs of a daemon repeat the same few secrets.
    mask_known("warm", values)
    sets_made: int = _ordered.cache_info().misses
    forms_made: int = forms_of.cache_info().misses
    shown: str = mask_known(planted, values)
    assert len(planted) > LONG_OUTPUT_CHARS
    assert quote_plus(SECRET) not in shown and shown.count("***") == 1
    assert _ordered.cache_info().misses == sets_made and forms_of.cache_info().misses == forms_made
    passes: int = len(held_forms(values, KNOWN_SECRET_MIN_CHARS))
    assert 0 < passes <= 60 * len(values), passes
