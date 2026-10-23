"""The settings of the reactions on Dot's messages (item 586): what a reaction does after a run and the switch of the
follow-ups are written in place under ``[reactions]`` with the file's comments kept, a file without the table gets it,
and a value the daemon cannot run on, or a third thing a reaction might do, is refused when the file is read."""

import tomllib
from pathlib import Path
from typing import get_args

import pytest
from pydantic import JsonValue, ValidationError

from dotagent.config import AppConfig, ReactionsConfig, load_default_config
from dotagent.config_edit import ReactionsSetting, set_setting
from dotagent.core.types import JsonObject
from dotagent.server.views import ReactionSettingRequest


def test_the_defaults_start_a_run_for_what_is_wrong_what_is_to_be_redone_and_what_is_to_be_explained() -> None:
    reactions = load_default_config().reactions
    assert (reactions.wrong, reactions.again, reactions.explain) == ("follow_up", "follow_up", "follow_up")
    assert (reactions.good, reactions.love, reactions.hold) == ("feedback", "feedback", "feedback")
    assert (reactions.follow_ups, reactions.follow_ups_per_hour) == (True, 20)


def test_what_a_reaction_does_after_a_run_is_written_under_reactions_with_the_comments_kept(tmp_path: Path) -> None:
    path: Path = tmp_path / "config.toml"
    path.write_text('# mine\n[reactions]\nwrong = "follow_up"  # a thumb down\nfollow_ups = true\n', encoding="utf-8")
    set_setting(path, "reactions", "wrong", "feedback")
    set_setting(path, "reactions", "follow_ups", False)
    set_setting(path, "reactions", "good", "follow_up")
    assert path.read_text(encoding="utf-8") == (
        '# mine\n[reactions]\ngood = "follow_up"\nwrong = "feedback"  # a thumb down\nfollow_ups = false\n'
    )
    assert tomllib.loads(path.read_text(encoding="utf-8"))["reactions"] == {
        "good": "follow_up",
        "wrong": "feedback",
        "follow_ups": False,
    }


def test_the_route_takes_the_keys_the_file_edit_writes_and_they_are_the_settings_the_owner_may_change() -> None:
    """One list of keys: the route's request is typed with the one ``set_setting`` takes, and those are the settings of
    the table but its two limits, which are set in the file."""
    assert ReactionSettingRequest.model_fields["key"].annotation is ReactionsSetting
    assert set(get_args(ReactionsSetting.__value__)) == set(ReactionsConfig.model_fields) - {
        "follow_ups_per_hour",
        "hold_minutes",
    }


def test_a_file_without_reactions_gets_the_table_below_the_others(tmp_path: Path) -> None:
    path: Path = tmp_path / "config.toml"
    path.write_text('[models]\nmain = "openai:gpt-5.5"\n', encoding="utf-8")
    set_setting(path, "reactions", "follow_ups", False)
    assert path.read_text(encoding="utf-8") == '[models]\nmain = "openai:gpt-5.5"\n\n[reactions]\nfollow_ups = false\n'


def _with(key: str, value: JsonValue) -> JsonObject:
    """The default configuration as read from a file, with ``[reactions] key`` set to ``value``."""
    data: JsonObject = load_default_config().model_dump(mode="json")
    section = data["reactions"]
    assert isinstance(section, dict)
    section[key] = value
    return data


@pytest.mark.parametrize("key", ["follow_ups_per_hour", "hold_minutes"])
def test_a_limit_that_would_stop_every_reaction_or_every_hold_is_refused(key: str) -> None:
    with pytest.raises(ValidationError) as refused:
        AppConfig.model_validate(_with(key, 0))
    assert [error["loc"] for error in refused.value.errors()] == [("reactions", key)]


def test_a_reaction_does_something_after_a_run_that_the_daemon_knows_or_is_refused() -> None:
    """A reaction starts a follow-up run or is only feedback; a third word would be read as neither."""
    with pytest.raises(ValidationError) as refused:
        AppConfig.model_validate(_with("wrong", "fix_it"))
    assert [error["loc"] for error in refused.value.errors()] == [("reactions", "wrong")]
