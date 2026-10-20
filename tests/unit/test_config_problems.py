"""A configuration that fails validation says which settings and why: a key this version of Dot does not have is named
as one a newer Dot may add, so that a daemon of an older checkout refusing the file of a newer one reads like that."""

from pathlib import Path

import pytest

from dotagent.config import load_config
from dotagent.core.errors import ConfigError


def _refused(tmp_path: Path, text: str) -> ConfigError:
    path: Path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ConfigError) as refused:
        load_config(path)
    assert refused.value.context["path"] == str(path)
    return refused.value


def test_a_key_the_schema_does_not_have_is_named_as_one_a_newer_dot_may_add(tmp_path: Path) -> None:
    error: ConfigError = _refused(tmp_path, '[web]\nsearxng_urls = "http://127.0.0.1:8888"\n')
    assert error.message == (
        "configuration failed validation: web.searxng_urls is not a setting this version of Dot knows "
        "(a newer Dot may add it)"
    )
    assert "extra_forbidden" in str(error.context["errors"])


def test_another_error_is_named_by_its_path_and_a_long_list_is_cut(tmp_path: Path) -> None:
    assert _refused(tmp_path, "[server]\nport = 0\n").message.startswith(
        "configuration failed validation: server.port: "
    )
    many: ConfigError = _refused(tmp_path, "".join(f"setting_{number} = 1\n" for number in range(7)))
    assert many.message.count(" is not a setting this version of Dot knows") == 5
    assert many.message.endswith("; and 2 more")
