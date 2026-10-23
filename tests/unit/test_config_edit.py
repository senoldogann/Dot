"""Switching ``[browser] mode``, ``raise_window`` and ``debug_port`` (item 265), the default model (``[models] main``,
item 199), the models of Dot's own work (``[models] fast``, ``review`` and ``vision``, item 232) and whether the daemon
resumes the runs it cut off (``[agent] resume``, item 231), and the names of ``[server] reach`` (item 271), in the
user's config.toml: the comments and the other settings stay as written."""

import stat
import tomllib
from pathlib import Path

import pytest

from dotagent.config import BrowserMode
from dotagent.config_edit import (
    set_browser_mode,
    set_browser_setting,
    set_default_model,
    set_embeddings,
    set_judge_provider,
    set_resume,
    set_role_model,
    set_setting,
    set_shell_sandbox,
    set_standby_models,
    with_browser_mode,
    with_browser_setting,
)
from dotagent.core.errors import ConfigError
from dotagent.core.types import JsonObject


def test_an_empty_file_gets_a_browser_table() -> None:
    assert with_browser_mode("", "attach") == '[browser]\nmode = "attach"\n'


def test_a_table_is_added_below_the_other_tables_with_a_blank_line_between() -> None:
    text: str = '# my settings\n[models]\nmain = "ollama:x"\n'
    assert with_browser_mode(text, "attach") == text + '\n[browser]\nmode = "attach"\n'


def test_a_browser_table_without_a_mode_gets_one_right_below_its_header() -> None:
    text: str = "[browser]\nheadless = true\n\n[agent]\nstall_limit = 4\n"
    assert with_browser_mode(text, "attach") == (
        '[browser]\nmode = "attach"\nheadless = true\n\n[agent]\nstall_limit = 4\n'
    )


def test_a_header_with_a_comment_is_found() -> None:
    text: str = "[browser]  # where pages open\nheadless = true\n"
    assert with_browser_mode(text, "attach") == '[browser]  # where pages open\nmode = "attach"\nheadless = true\n'


def test_an_existing_mode_is_replaced_and_the_comment_after_it_stays() -> None:
    text: str = '[browser]\nheadless = true\nmode = "own"  # "own" or "attach"\n'
    assert with_browser_mode(text, "attach") == '[browser]\nheadless = true\nmode = "attach"  # "own" or "attach"\n'


def test_a_mode_in_single_quotes_is_replaced() -> None:
    assert with_browser_mode("[browser]\nmode = 'own'\n", "attach") == '[browser]\nmode = "attach"\n'


def test_a_mode_of_another_table_is_left_alone() -> None:
    text: str = '[agent]\nmode = "x"\n\n[browser]\nheadless = true\n\n[policy]\nmode = "y"\n'
    assert with_browser_mode(text, "attach") == (
        '[agent]\nmode = "x"\n\n[browser]\nmode = "attach"\nheadless = true\n\n[policy]\nmode = "y"\n'
    )


@pytest.mark.parametrize("mode", ["own", "attach"])
@pytest.mark.parametrize(
    "text",
    [
        "",
        "[browser]\n",
        '[browser]\nmode = "own"\n',
        '[browser]\nmode = "attach"  # keep\nheadless = true\n',
        '[models]\nmain = "ollama:x"\n',
        '[models]\nmain = "ollama:x"\n\n[browser]\nheadless = true\n\n[agent]\nstall_limit = 4\n',
    ],
)
def test_the_result_is_valid_toml_that_holds_the_new_mode(text: str, mode: BrowserMode) -> None:
    result: JsonObject = tomllib.loads(with_browser_mode(text, mode))
    browser: object = result["browser"]
    assert isinstance(browser, dict)
    assert browser["mode"] == mode


def test_the_file_is_changed_in_place_and_keeps_its_permissions(tmp_path: Path) -> None:
    path: Path = tmp_path / "config.toml"
    path.write_text("# mine\n[browser]\nheadless = true\n", encoding="utf-8")
    path.chmod(0o640)
    set_browser_mode(path, "attach")
    assert path.read_text(encoding="utf-8") == '# mine\n[browser]\nmode = "attach"\nheadless = true\n'
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert list(tmp_path.iterdir()) == [path]
    set_browser_mode(path, "attach")
    assert path.read_text(encoding="utf-8") == '# mine\n[browser]\nmode = "attach"\nheadless = true\n'
    set_browser_mode(path, "own")
    assert path.read_text(encoding="utf-8") == '# mine\n[browser]\nmode = "own"\nheadless = true\n'


def test_a_missing_file_and_its_directory_are_created_private(tmp_path: Path) -> None:
    path: Path = tmp_path / "Dot" / "config.toml"
    set_browser_mode(path, "attach")
    assert path.read_text(encoding="utf-8") == '[browser]\nmode = "attach"\n'
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_a_file_that_is_not_toml_is_refused_and_left_as_it_was(tmp_path: Path) -> None:
    path: Path = tmp_path / "config.toml"
    path.write_text("[browser\nmode = \n", encoding="utf-8")
    with pytest.raises(ConfigError, match="not valid TOML"):
        set_browser_mode(path, "attach")
    assert path.read_text(encoding="utf-8") == "[browser\nmode = \n"


def test_a_file_that_is_not_utf8_is_refused_as_a_config_error_and_left_as_it_was(tmp_path: Path) -> None:
    path: Path = tmp_path / "config.toml"
    path.write_bytes(b'[browser]\nmode = "\xff"\n')
    with pytest.raises(ConfigError, match="could not be changed automatically"):
        set_browser_mode(path, "attach")
    assert path.read_bytes() == b'[browser]\nmode = "\xff"\n'


def test_a_browser_table_written_as_an_inline_table_is_refused_and_left_as_it_was(tmp_path: Path) -> None:
    original: str = "browser = { headless = true }\n"
    path: Path = tmp_path / "config.toml"
    path.write_text(original, encoding="utf-8")
    with pytest.raises(ConfigError, match='add mode = "attach" under \\[browser\\] by hand'):
        set_browser_mode(path, "attach")
    assert path.read_text(encoding="utf-8") == original


def test_raise_window_is_added_or_replaced_like_the_mode_and_the_mode_stays(tmp_path: Path) -> None:
    assert with_browser_setting("", "raise_window", True) == "[browser]\nraise_window = true\n"
    text: str = '[browser]\nmode = "attach"\nraise_window = true  # keep\n\n[agent]\nstall_limit = 4\n'
    assert with_browser_setting(text, "raise_window", False) == (
        '[browser]\nmode = "attach"\nraise_window = false  # keep\n\n[agent]\nstall_limit = 4\n'
    )
    path: Path = tmp_path / "config.toml"
    path.write_text('[browser]\nmode = "attach"\n', encoding="utf-8")
    set_browser_setting(path, "raise_window", True)
    assert path.read_text(encoding="utf-8") == '[browser]\nraise_window = true\nmode = "attach"\n'
    set_browser_mode(path, "own")
    assert path.read_text(encoding="utf-8") == '[browser]\nraise_window = true\nmode = "own"\n'


def test_the_devtools_port_is_written_as_a_number_below_the_header() -> None:
    written: str = with_browser_setting('[browser]\nmode = "own"\n', "debug_port", 9222)
    assert written == '[browser]\ndebug_port = 9222\nmode = "own"\n'
    assert tomllib.loads(written)["browser"] == {"debug_port": 9222, "mode": "own"}


def test_a_raise_window_written_as_a_string_is_replaced_by_the_boolean(tmp_path: Path) -> None:
    path: Path = tmp_path / "config.toml"
    path.write_text('[browser]\nraise_window = "yes"  # typo\n', encoding="utf-8")
    set_browser_setting(path, "raise_window", True)
    assert path.read_text(encoding="utf-8") == "[browser]\nraise_window = true  # typo\n"


def test_the_default_model_is_replaced_under_models_and_the_other_tables_stay(tmp_path: Path) -> None:
    path: Path = tmp_path / "config.toml"
    path.write_text(
        '[browser]\nmain = "x"\n\n[models]\nfast = "ollama:quick"\nmain = "ollama:old"  # the default\n',
        encoding="utf-8",
    )
    set_default_model(path, "anthropic:claude-x")
    assert path.read_text(encoding="utf-8") == (
        '[browser]\nmain = "x"\n\n[models]\nfast = "ollama:quick"\nmain = "anthropic:claude-x"  # the default\n'
    )


def test_a_file_without_models_gets_the_table_with_the_default_model(tmp_path: Path) -> None:
    path: Path = tmp_path / "config.toml"
    path.write_text("[browser]\nheadless = true\n", encoding="utf-8")
    set_default_model(path, "ollama:glm")
    assert path.read_text(encoding="utf-8") == '[browser]\nheadless = true\n\n[models]\nmain = "ollama:glm"\n'
    missing: Path = tmp_path / "Dot" / "config.toml"
    set_default_model(missing, "ollama:glm")
    assert missing.read_text(encoding="utf-8") == '[models]\nmain = "ollama:glm"\n'


def test_a_models_table_written_inline_is_refused_and_left_as_it_was(tmp_path: Path) -> None:
    original: str = 'models = { main = "ollama:old" }\n'
    path: Path = tmp_path / "config.toml"
    path.write_text(original, encoding="utf-8")
    with pytest.raises(ConfigError, match='add main = "ollama:new" under \\[models\\] by hand'):
        set_default_model(path, "ollama:new")
    assert path.read_text(encoding="utf-8") == original


def test_resume_is_written_under_agent_and_replaced_there_and_the_other_tables_stay(tmp_path: Path) -> None:
    path: Path = tmp_path / "config.toml"
    path.write_text('[models]\nmain = "ollama:x"\n', encoding="utf-8")
    set_resume(path, "safe")
    assert path.read_text(encoding="utf-8") == '[models]\nmain = "ollama:x"\n\n[agent]\nresume = "safe"\n'
    path.write_text('[agent]\ngoal_mode = true\nresume = "safe"  # mine\n\n[models]\nresume = "x"\n', encoding="utf-8")
    set_resume(path, "off")
    assert path.read_text(encoding="utf-8") == (
        '[agent]\ngoal_mode = true\nresume = "off"  # mine\n\n[models]\nresume = "x"\n'
    )


def test_a_model_of_dots_own_work_is_replaced_under_models_and_the_others_stay(tmp_path: Path) -> None:
    path: Path = tmp_path / "config.toml"
    path.write_text('[models]\nmain = "ollama:x"\nreview = "ollama:old"  # mine\n', encoding="utf-8")
    set_role_model(path, "review", "anthropic:claude-x")
    set_role_model(path, "vision", "ollama:llava")
    assert path.read_text(encoding="utf-8") == (
        '[models]\nvision = "ollama:llava"\nmain = "ollama:x"\nreview = "anthropic:claude-x"  # mine\n'
    )


def test_the_judges_provider_is_written_under_judge_and_the_rest_stays(tmp_path: Path) -> None:
    path: Path = tmp_path / "config.toml"
    path.write_text('[models]\nmain = "ollama:x"\n', encoding="utf-8")
    set_judge_provider(path, "typesafe")
    assert path.read_text(encoding="utf-8") == '[models]\nmain = "ollama:x"\n\n[judge]\nprovider = "typesafe"\n'
    path.write_text(
        '[judge]\nprovider = "typesafe"  # mine\ntimeout_s = 5\n\n[models]\nprovider = "x"\n', encoding="utf-8"
    )
    set_judge_provider(path, "none")
    assert path.read_text(encoding="utf-8") == (
        '[judge]\nprovider = "none"  # mine\ntimeout_s = 5\n\n[models]\nprovider = "x"\n'
    )


def test_the_sandbox_is_written_under_shell_as_a_boolean_and_the_rest_stays(tmp_path: Path) -> None:
    path: Path = tmp_path / "config.toml"
    path.write_text('[models]\nmain = "ollama:x"\n', encoding="utf-8")
    set_shell_sandbox(path, False)
    assert path.read_text(encoding="utf-8") == '[models]\nmain = "ollama:x"\n\n[shell]\nsandbox = false\n'
    path.write_text("[shell]\npass_env = []  # mine\nsandbox = false\n\n[web]\nsandbox = 1\n", encoding="utf-8")
    set_shell_sandbox(path, True)
    assert path.read_text(encoding="utf-8") == "[shell]\npass_env = []  # mine\nsandbox = true\n\n[web]\nsandbox = 1\n"


def test_a_list_of_names_is_written_and_replaced_with_the_comment_after_it_kept(tmp_path: Path) -> None:
    path: Path = tmp_path / "config.toml"
    path.write_text("[server]\nport = 8765\n", encoding="utf-8")
    set_setting(path, "server", "reach", ["a.example.ts.net"])
    assert path.read_text(encoding="utf-8") == '[server]\nreach = ["a.example.ts.net"]\nport = 8765\n'
    path.write_text('[server]\n  reach = ["a.example.ts.net"]  # the tailnet\n', encoding="utf-8")
    set_setting(path, "server", "reach", ["a.example.ts.net", "b.example.ts.net"])
    assert path.read_text(encoding="utf-8") == (
        '[server]\n  reach = ["a.example.ts.net", "b.example.ts.net"]  # the tailnet\n'
    )
    assert tomllib.loads(path.read_text(encoding="utf-8"))["server"]["reach"] == [
        "a.example.ts.net",
        "b.example.ts.net",
    ]


def test_a_setting_of_snapshots_is_written_under_its_own_table(tmp_path: Path) -> None:
    """Settings › SnapShots writes ``[snapshots]`` (item 405): a choice as a string, a switch as a bool."""
    path: Path = tmp_path / "config.toml"
    path.write_text('[models]\nmain = "ollama:x"\n', encoding="utf-8")
    set_setting(path, "snapshots", "sound", "pop")
    set_setting(path, "snapshots", "app_text", False)
    written: JsonObject = tomllib.loads(path.read_text(encoding="utf-8"))
    assert written["snapshots"] == {"sound": "pop", "app_text": False}
    assert written["models"] == {"main": "ollama:x"}


def test_a_name_typed_with_a_quote_or_a_backslash_reads_back_as_it_was_written(tmp_path: Path) -> None:
    """Item 420: a name the owner types goes to the file as a TOML basic string, escaped."""
    path: Path = tmp_path / "config.toml"
    typed: str = 'Ada "Al" Mi\\ro'
    set_setting(path, "assistant", "name", typed)
    assert tomllib.loads(path.read_text(encoding="utf-8"))["assistant"]["name"] == typed
    # A value with escapes in it is found whole when it is changed again, and a comment after it is kept.
    path.write_text('[assistant]\nname = "Ada \\"Al\\" Mi\\\\ro"  # the name\n', encoding="utf-8")
    set_setting(path, "assistant", "name", "Miro")
    written: str = path.read_text(encoding="utf-8")
    assert tomllib.loads(written)["assistant"]["name"] == "Miro" and "# the name" in written, written


def test_a_list_written_over_several_lines_is_replaced_whole(tmp_path: Path) -> None:
    """Item 558: ``standby`` and ``reach`` formatted the usual multi-line way were refused, as only the first line of
    the list was replaced."""
    path: Path = tmp_path / "config.toml"
    path.write_text(
        '[models]\nmain = "ollama:x"\nstandby = [\n  "ollama:a:cloud",  # the first\n  "ollama:b:cloud",\n]  # mine\n'
        '\n[server]\nreach = [\n  "a.example.ts.net",\n]\nport = 8765\n',
        encoding="utf-8",
    )
    set_standby_models(path, ["ollama:c:cloud"])
    set_setting(path, "server", "reach", [])
    assert path.read_text(encoding="utf-8") == (
        '[models]\nmain = "ollama:x"\nstandby = ["ollama:c:cloud"]  # mine\n\n[server]\nreach = []\nport = 8765\n'
    )


def test_a_bracket_in_a_name_or_a_comment_does_not_end_the_list(tmp_path: Path) -> None:
    path: Path = tmp_path / "config.toml"
    path.write_text(
        "[server]\nreach = [\n  \"a]b.example\", # one ] too many\n  'c]',\n]\nport = 8765\n", encoding="utf-8"
    )
    set_setting(path, "server", "reach", ["d.example"])
    assert path.read_text(encoding="utf-8") == '[server]\nreach = ["d.example"]\nport = 8765\n'


def test_a_list_that_never_closes_is_refused_and_left_as_it_was(tmp_path: Path) -> None:
    path: Path = tmp_path / "config.toml"
    text: str = '[server]\nreach = [\n  "a.example",\n'
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError):
        set_setting(path, "server", "reach", ["b.example"])
    assert path.read_text(encoding="utf-8") == text


def test_whether_the_notes_are_found_by_meaning_is_written_under_memory(tmp_path: Path) -> None:
    """Item 558: ``dotx setup`` switches embeddings off on a Mac that has no Ollama to run them."""
    path: Path = tmp_path / "config.toml"
    path.write_text('[models]\nmain = "openai:gpt-5.5"\n', encoding="utf-8")
    set_embeddings(path, False)
    assert path.read_text(encoding="utf-8") == '[models]\nmain = "openai:gpt-5.5"\n\n[memory]\nembed_enabled = false\n'
    set_embeddings(path, True)
    assert tomllib.loads(path.read_text(encoding="utf-8"))["memory"] == {"embed_enabled": True}
