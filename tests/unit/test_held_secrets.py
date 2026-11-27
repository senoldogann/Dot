"""The secrets a chat holds (item 857): the value the owner typed on a run's card is in the chat's own file alone,
readable by the owner alone, for a day; it is masked from the moment it is saved and again once a daemon starts and
reads the file; a name the shell or the loader reads is refused, and so is a value too short to mask. The values here
are made up; the files are in a Dot home of the test's own."""

import json
import os
import stat
from pathlib import Path

import pytest

from dotagent import secrets
from dotagent.agent.held_secrets import (
    HOLD_S,
    SecretAnswer,
    all_held_values,
    card_answers,
    checked_name,
    forget_secret,
    forget_secrets,
    held_dir,
    held_secrets,
    held_values,
    mask_all_held,
    save_secret,
    save_secrets,
)
from dotagent.core.errors import InvalidValueError
from dotagent.core.types import JsonObject

VALUE: str = "made-up-token-0123456789-item-857"
THREAD: str = "thr_test_857"


@pytest.fixture(autouse=True)
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("DOT_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(secrets, "_filled", frozenset())
    return tmp_path / "home"


def test_a_secret_is_held_for_its_chat_alone_in_a_file_of_its_own_for_a_day(home: Path) -> None:
    held = save_secret(THREAD, "DEMO_TOKEN", "Demo token", VALUE, now=1000.0)
    assert (held.name, held.label, held.until) == ("DEMO_TOKEN", "Demo token", 1000.0 + HOLD_S)
    path: Path = home / "held-secrets" / f"{THREAD}.json"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700
    # The chat's commands read it; another chat's do not; a run reads its name, never its value.
    assert held_values(THREAD, now=1001.0) == {"DEMO_TOKEN": VALUE}
    assert held_values("thr_other", now=1001.0) == {}
    assert [one.name for one in held_secrets(THREAD, now=1001.0)] == ["DEMO_TOKEN"]
    assert VALUE not in repr(held_secrets(THREAD, now=1001.0))
    # A day on, it holds no more.
    assert held_values(THREAD, now=1000.0 + HOLD_S + 1) == {}
    # It is in no other file of the home.
    others: list[Path] = [file for file in home.rglob("*") if file.is_file() and file != path]
    assert all(VALUE not in file.read_text(errors="ignore") for file in others)


def test_it_is_masked_once_saved_and_again_once_a_daemon_reads_the_files(
    home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    save_secret(THREAD, "DEMO_TOKEN", "Demo token", VALUE)
    assert VALUE in secrets.known_secrets(filled=True)
    # A mask, no more: what refuses or chooses by a value never judges a run by one it never saw.
    assert VALUE not in secrets.known_secrets(filled=False)
    # A daemon starts with no masks, and reads them from the files.
    monkeypatch.setattr(secrets, "_filled", frozenset())
    assert VALUE not in secrets.known_secrets(filled=True)
    assert mask_all_held() == 1
    assert VALUE in secrets.known_secrets(filled=True)


def test_a_file_that_holds_nothing_live_is_removed_as_a_daemon_starts(home: Path) -> None:
    save_secret(THREAD, "DEMO_TOKEN", "Demo token", VALUE, now=1000.0)
    path: Path = home / "held-secrets" / f"{THREAD}.json"
    assert path.exists()
    assert mask_all_held(now=1000.0 + HOLD_S + 1) == 0
    assert not path.exists()


def test_a_second_secret_of_the_name_replaces_the_first(home: Path) -> None:
    save_secret(THREAD, "DEMO_TOKEN", "Demo token", VALUE, now=1000.0)
    save_secret(THREAD, "DEMO_TOKEN", "Demo token", VALUE + "-new", now=2000.0)
    save_secret(THREAD, "OTHER_KEY", "Other", "another-made-up-key-0001", now=2000.0)
    assert held_values(THREAD, now=2001.0) == {"DEMO_TOKEN": VALUE + "-new", "OTHER_KEY": "another-made-up-key-0001"}


@pytest.mark.parametrize(
    "name",
    [
        "PATH",
        "HOME",
        "DYLD_INSERT_LIBRARIES",
        "LD_PRELOAD",
        "GIT_SSH_COMMAND",
        "BASH_ENV",
        "NODE_OPTIONS",
        "DOT_HOME",
        "HTTPS_PROXY",
        "SSLKEYLOGFILE",
        "NODE_TLS_REJECT_UNAUTHORIZED",
    ],
)
def test_a_name_the_shell_or_the_loader_reads_is_refused(name: str) -> None:
    with pytest.raises(InvalidValueError):
        checked_name(name)
    with pytest.raises(InvalidValueError):
        save_secret(THREAD, name, "x", VALUE)


@pytest.mark.parametrize("name", ["lower_case", "1STARTS_WITH_A_DIGIT", "HAS-DASH", "A", "X" * 65, "WITH SPACE"])
def test_a_name_that_is_no_environment_variables_is_refused(name: str) -> None:
    with pytest.raises(InvalidValueError):
        checked_name(name)


def test_a_value_too_short_to_mask_or_a_chat_id_that_is_a_path_is_refused(home: Path) -> None:
    with pytest.raises(InvalidValueError):
        save_secret(THREAD, "DEMO_TOKEN", "Demo token", "short")
    with pytest.raises(InvalidValueError):
        save_secret("../escape", "DEMO_TOKEN", "Demo token", VALUE)
    assert not (home / "held-secrets").exists()


def test_a_value_is_held_without_the_spaces_around_it_and_a_forgotten_one_holds_no_more(home: Path) -> None:
    save_secret(THREAD, "DEMO_TOKEN", "Demo token", f"  {VALUE}\n", now=1000.0)
    assert held_values(THREAD, now=1001.0) == {"DEMO_TOKEN": VALUE}
    assert VALUE in secrets.known_secrets(filled=True)
    # The card was answered otherwise meanwhile: what it held is taken back.
    forget_secret(
        save_secret(THREAD, "OTHER_KEY", "Other", "another-made-up-key-0001", now=1000.0),
        THREAD,
        "another-made-up-key-0001",
        now=1001.0,
    )
    assert held_values(THREAD, now=1001.0) == {"DEMO_TOKEN": VALUE}


def test_a_secret_taken_back_puts_back_the_one_it_replaced_and_leaves_another_tabs(home: Path) -> None:
    save_secret(THREAD, "DEMO_TOKEN", "Demo token", VALUE, now=1000.0)
    newer: str = VALUE + "-newer"
    saved = save_secret(THREAD, "DEMO_TOKEN", "Demo token", newer, now=1100.0)
    assert VALUE not in repr(saved) and VALUE not in saved.model_dump_json()
    forget_secret(saved, THREAD, newer, now=1101.0)
    assert held_values(THREAD, now=1101.0) == {"DEMO_TOKEN": VALUE}
    # Two tabs: the one that lost takes back nothing of the value the other saved after it.
    lost = save_secret(THREAD, "DEMO_TOKEN", "Demo token", newer, now=1200.0)
    save_secret(THREAD, "DEMO_TOKEN", "Demo token", VALUE + "-won", now=1201.0)
    forget_secret(lost, THREAD, newer, now=1202.0)
    assert held_values(THREAD, now=1202.0) == {"DEMO_TOKEN": VALUE + "-won"}


def test_every_chats_values_are_read_from_their_files_to_mask(home: Path) -> None:
    save_secret(THREAD, "DEMO_TOKEN", "Demo token", VALUE, now=1000.0)
    save_secret("thr_other", "OTHER_KEY", "Other", "another-made-up-key-0001", now=1000.0)
    assert sorted(all_held_values(now=1001.0)) == sorted([VALUE, "another-made-up-key-0001"])
    assert all_held_values(now=1000.0 + HOLD_S + 1) == ()


def test_the_folder_is_guarded_from_every_run(home: Path) -> None:
    from dotagent.config import load_default_config
    from dotagent.runtime import _guarded_files

    assert held_dir() in _guarded_files(load_default_config())


def test_a_name_the_shell_reads_written_into_the_file_by_other_means_is_not_given_to_a_command(home: Path) -> None:
    """A yolo run may write any file, this one too: what it writes there sets no name the shell or the loader reads."""
    save_secret(THREAD, "DEMO_TOKEN", "Demo token", VALUE, now=1000.0)
    path: Path = home / "held-secrets" / f"{THREAD}.json"
    entries = json.loads(path.read_text(encoding="utf-8"))
    entries["PATH"] = {"value": "/tmp/planted:/usr/bin", "label": "x", "until": 1e12}
    entries["HTTPS_PROXY"] = {"value": "http://planted.example:8080", "label": "x", "until": 1e12}
    path.write_text(json.dumps(entries), encoding="utf-8")
    assert held_values(THREAD, now=1001.0) == {"DEMO_TOKEN": VALUE}


# Item 2040: several fields on one card, one of them a key file.
_PEM: str = (
    "-----BEGIN PRIVATE KEY-----\n"
    "MIGTAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBHkwdwIBAQQgmadeUpKeyLineOne\n"
    "oAoGCCqGSM49AwEHoUQDQgAEmadeUpKeyLineTwoForTheTestOnly0123456789\n"
    "-----END PRIVATE KEY-----\n"
)


def _answers() -> list[SecretAnswer]:
    return [
        SecretAnswer(name="ASC_ISSUER_ID", label="Issuer ID", kind="text", value="69a6de70-made-up-issuer-0123"),
        SecretAnswer(name="ASC_KEY_ID", label="Key ID", kind="secret", value="MADEUPKEY1"),
        SecretAnswer(name="ASC_KEY", label="Key file", kind="file", value=_PEM),
    ]


def test_a_cards_fields_are_held_together_in_one_write_each_masked_and_a_files_text_with_its_lines(home: Path) -> None:
    saved = save_secrets(THREAD, _answers(), now=1000.0)
    assert [one.name for one in saved] == ["ASC_ISSUER_ID", "ASC_KEY_ID", "ASC_KEY"]
    values = held_values(THREAD, now=1001.0)
    assert values["ASC_KEY"] == _PEM.strip(), "the file's text, its inner lines kept"
    assert values["ASC_ISSUER_ID"] == "69a6de70-made-up-issuer-0123"
    masked: str = secrets.mask_filled(
        "id 69a6de70-made-up-issuer-0123 key MADEUPKEY1 line "
        "MIGTAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBHkwdwIBAQQgmadeUpKeyLineOne "
        "-----BEGIN PRIVATE KEY-----"
    )
    assert "made-up-issuer" not in masked and "MADEUPKEY1" not in masked and "madeUpKeyLineOne" not in masked, masked
    assert "-----BEGIN PRIVATE KEY-----" in masked, "a PEM's first line is no secret"
    # A daemon that starts masks the same parts from the file.
    started: set[str] = set(all_held_values(now=1001.0))
    assert "MIGTAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBHkwdwIBAQQgmadeUpKeyLineOne" in started
    assert "-----END PRIVATE KEY-----" not in started


def test_one_field_that_cannot_be_held_holds_none_of_the_card(home: Path) -> None:
    short = [*_answers()[:2], SecretAnswer(name="ASC_KEY", label="Key file", kind="file", value="short")]
    with pytest.raises(InvalidValueError, match="shorter than 8"):
        save_secrets(THREAD, short, now=1000.0)
    assert held_values(THREAD, now=1001.0) == {}
    twice = [*_answers()[:2], SecretAnswer(name="ASC_KEY_ID", label="Again", kind="secret", value="another-value-123")]
    with pytest.raises(InvalidValueError, match="names each secret once"):
        save_secrets(THREAD, twice, now=1000.0)
    lines = [SecretAnswer(name="DEMO_TOKEN", label="Token", kind="secret", value="line-one-12345\nline-two-12345")]
    with pytest.raises(InvalidValueError, match="one line"):
        save_secrets(THREAD, lines, now=1000.0)
    assert not (home / "held-secrets" / f"{THREAD}.json").exists()


def test_a_card_taken_back_puts_back_every_field_in_one_write(home: Path) -> None:
    save_secret(THREAD, "ASC_KEY_ID", "Key ID", "older-key-id-value", now=1000.0)
    saved = save_secrets(THREAD, _answers(), now=1001.0)
    forget_secrets(saved, THREAD, {answer.name: answer.value for answer in _answers()}, now=1002.0)
    assert held_values(THREAD, now=1003.0) == {"ASC_KEY_ID": "older-key-id-value"}


def test_a_card_is_answered_by_its_fields_alone() -> None:
    fields: JsonObject = {
        "label": "App Store Connect key",
        "fields": [
            {"name": "ASC_ISSUER_ID", "label": "Issuer ID", "kind": "text"},
            {"name": "ASC_KEY", "label": "Key file", "kind": "file"},
        ],
    }
    answers = card_answers(fields, None, {"ASC_ISSUER_ID": "issuer-0123456789", "ASC_KEY": _PEM})
    assert [(one.name, one.kind) for one in answers] == [("ASC_ISSUER_ID", "text"), ("ASC_KEY", "file")]
    with pytest.raises(InvalidValueError, match="update the app"):
        card_answers(fields, "one-value-0123456789", None)
    with pytest.raises(InvalidValueError, match="every field"):
        card_answers(fields, None, {"ASC_ISSUER_ID": "issuer-0123456789"})
    with pytest.raises(InvalidValueError, match="every field"):
        card_answers(fields, None, {"ASC_ISSUER_ID": "issuer-0123456789", "ASC_KEY": _PEM, "OTHER": "x" * 10})
    with pytest.raises(InvalidValueError, match="value or with its fields"):
        card_answers(fields, "x" * 10, {"ASC_KEY": _PEM})
    # A card asked before item 2040: one name, answered with one value.
    old = card_answers({"name": "DEMO_TOKEN", "label": "Demo"}, VALUE, None)
    assert [(one.name, one.kind, one.value) for one in old] == [("DEMO_TOKEN", "secret", VALUE)]


def test_a_file_field_takes_a_key_in_pem_alone(home: Path) -> None:
    """The security review of item 2040: a JSON's escaped key, or an .env's short values, escape the line masks."""
    for text in (
        '{"type": "service_account", "private_key": "-----BEGIN PRIVATE KEY-----\\nMIIEv..."}',
        "API_KEY=abc123456\n",
    ):
        answer = SecretAnswer(name="GCP_KEY", label="Key file", kind="file", value=text)
        with pytest.raises(InvalidValueError, match="key in PEM"):
            save_secrets(THREAD, [answer], now=1000.0)
    assert held_values(THREAD, now=1001.0) == {}


def test_a_key_file_is_read_line_by_line_so_a_long_odd_text_is_refused_at_once() -> None:
    """Item 2040: the PEM check is one pass; a 16 KB text that only starts like a key is refused, a key taken."""
    import time

    from dotagent.agent.held_secrets import is_pem_block

    started = time.monotonic()
    refused = [
        is_pem_block("-----BEGIN X-----" + " " * 16000 + "x"),
        is_pem_block("-----BEGIN X-----\n" + "AB \n" * 3000 + "!"),
    ]

    assert refused == [False, False]
    assert time.monotonic() - started < 0.5
    assert is_pem_block("-----BEGIN PRIVATE KEY-----\nMIGTAgEA\nAwEHBG0w\n-----END PRIVATE KEY-----\n")
