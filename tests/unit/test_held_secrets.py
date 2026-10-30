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
    all_held_values,
    checked_name,
    forget_secret,
    held_dir,
    held_secrets,
    held_values,
    mask_all_held,
    save_secret,
)
from dotagent.core.errors import InvalidValueError

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
