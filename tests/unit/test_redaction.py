"""The redactor masks the secrets it knows by their forms and keeps the words around them, and leaves look-alikes alone
(items 214 and 241). Every sample is made up, and those in the form of a real service's key are put together at run time, so no
file holds one whole."""

import base64
import time

import pytest

from dotagent.core.text import redact_json, redact_secrets

_MOMENT_S: float = 1.0
_LETTERS: str = "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789aBcDeFgHiJ"
_PEM_BODY: str = "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC" + "x" * 64

_MASKED: list[tuple[str, str]] = [
    (f"cloned with {'gh' + 'p_' + _LETTERS[:36]} yesterday", "cloned with *** yesterday"),
    (f"token: {'github' + '_pat_' + _LETTERS[:30]}", "token: ***"),
    (f"key id {'AK' + 'IA' + 'QWERTYUIOPASDFGH'} in use", "key id *** in use"),
    (f"maps key {'AI' + 'za' + _LETTERS[:35]}", "maps key ***"),
    (f"bot {'xo' + 'xb-' + '1234567890-' + _LETTERS[:20]}", "bot ***"),
    (f"jwt {'ey' + 'JhbGciOiJIUzI1NiJ9'}.{'ey' + 'JzdWIiOiIxMjM0NSJ9'}.{_LETTERS[:22]}", "jwt ***"),
    (f"Authorization: Basic {base64.b64encode(b'alice:wonderland').decode()}", "Authorization: Basic ***"),
    (f'{{"Authorization": "Basic {base64.b64encode(b"bob:builder").decode()}"}}', '{"Authorization": "Basic ***"}'),
    (
        f"key:\n-----BEGIN {'PRIVATE KEY'}-----\n{_PEM_BODY}\n-----END {'PRIVATE KEY'}-----\nnext",
        "key:\n***\nnext",
    ),
    (f"-----BEGIN {'OPENSSH PRIVATE KEY'}-----\n{_PEM_BODY}", "***"),
    ("git clone https://alice:s3cr3t-p4ss@example.com/repo.git", "git clone https://alice:***@example.com/repo.git"),
    ("postgres://app:p@ss@db.example.com/main", "postgres://app:***@db.example.com/main"),
    ("password: hunter2", "password: ***"),
    ('{"db_password": "short"}', '{"db_password": "***"}'),
    (f"aws_secret_access_key = {_LETTERS[:40]}", "aws_secret_access_key = ***"),
    (f"X-Dot-Token: {_LETTERS[:24]}", "X-Dot-Token: ***"),
    (f"OPENAI {'sk-' + 'proj-' + _LETTERS[:24]}", "OPENAI ***"),
    (f"Authorization: Bearer {_LETTERS[:24]}", "Authorization: ***"),
    (f"api_key={_LETTERS[:12]}", "api_key=***"),
    (f"charge with {'sk' + '_live_' + _LETTERS[:24]}", "charge with ***"),
    (f"restricted {'rk' + '_test_' + _LETTERS[:24]}", "restricted ***"),
    (f"webhook {'wh' + 'sec_' + _LETTERS[:32]}", "webhook ***"),
    (f"push with {'gl' + 'pat-' + _LETTERS[:20]}", "push with ***"),
    (f"publish with {'np' + 'm_' + _LETTERS[:36]}", "publish with ***"),
    (f"hub {'h' + 'f_' + _LETTERS[:34]} read", "hub *** read"),
    (f"twilio {'S' + 'K' + '0123456789abcdef' * 2}", "twilio ***"),
    (f"mail {'S' + 'G.' + _LETTERS[:22] + '.' + _LETTERS[:43]}", "mail ***"),
    (
        f"AccountName=dot;{'Account' + 'Key'}={_LETTERS * 2}==;EndpointSuffix=core.windows.net",
        "AccountName=dot;AccountKey=***",
    ),
    ('"private_key_id": "' + "0123456789abcdef" * 2 + '"', '"private_key_id": "***"'),
    (f"Authorization: token {_LETTERS[:40]}", "Authorization: token ***"),
    (f'{{"authorization": "ApiKey {_LETTERS[:32]}"}}', '{"authorization": "ApiKey ***"}'),
    (f"Authorization: {_LETTERS[:40]}", "Authorization: ***"),
    (f"Cookie: sessionid={_LETTERS[:32]}; theme=dark\nnext", "Cookie: ***\nnext"),
    (f"Set-Cookie: sid={_LETTERS[:24]}; Path=/; HttpOnly", "Set-Cookie: ***"),
    # The code review of 2026-10-15 (Y3): a value in single quotes, a key with a word after its secret, a header of a
    # Python dict.
    (f"export API_KEY='{_LETTERS[:19]}'", "export API_KEY='***'"),
    ("DB_PASSWORD='hunter2hunter2' ./migrate.sh", "DB_PASSWORD='***' ./migrate.sh"),
    (f"SECRET_KEY={_LETTERS[:20]}", "SECRET_KEY=***"),
    (f"{{'Authorization': 'Token {_LETTERS[:19]}'}}", "{'Authorization': 'Token ***'}"),
]
_KEPT: list[str] = [
    '"max_tokens": 4096',
    "the tokenizer splits words, tokens: 12",
    "Basic usage of the tool",
    "http://localhost:8765/api/status",
    "ssh://git@github.com/owner/repo.git",
    "mail alice@example.com about https://example.com/a@b",
    "passwords: 3 were reused",
    "pwd=/Users/someone/project",
    "AKIA alone is no key",
    "the risk_live_dashboard01 panel",
    "hf_hub_download(repo_id, filename)",
    "npm_config_registry=https://registry.npmjs.org/",
    "Cookie: the browser keeps it until it expires",
    "Authorization: required for every request",
    # Words that end in "sk" before a dash are no key (Y4).
    "https://example.com/blog/task-management-best-practices",
    "feature/ask-for-confirmation-before-delete",
    "token_count=12345",
]


@pytest.mark.parametrize(("text", "masked"), _MASKED)
def test_a_secret_of_a_known_form_is_masked_and_the_words_around_it_kept(text: str, masked: str) -> None:
    assert redact_secrets(text) == masked


@pytest.mark.parametrize("text", _KEPT)
def test_a_text_that_only_looks_like_a_secret_is_left_alone(text: str) -> None:
    assert redact_secrets(text) == text


def test_a_long_text_of_short_words_is_masked_in_a_moment() -> None:
    """Every event and error passes through the redactor, so no pattern may try each start of a long run of words
    joined by dots or dashes, or of the start of a web token, against the whole rest of it."""
    text: str = "a." * 20_000 + "b-" * 20_000 + "eyJ-" * 10_000 + " password: hunter2"
    started: float = time.monotonic()
    assert redact_secrets(text).endswith("password: ***")
    assert time.monotonic() - started < _MOMENT_S


def test_a_field_named_for_a_secret_is_masked_whatever_its_value_looks_like() -> None:
    assert redact_json(
        {"password": "hunter2hunter2", "db": {"api_key": "x"}, "max_tokens": "4096", "note": "token"}
    ) == {
        "password": "***",
        "db": {"api_key": "***"},
        "max_tokens": "4096",
        "note": "token",
    }
