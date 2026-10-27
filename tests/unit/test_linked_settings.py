"""A ``config.toml`` or ``.env`` the owner keeps elsewhere and links into Dot's home (a dotfiles repository) is changed
where the link leads, and stays a link (item 728, the outside review of 2026-10-27): the write had replaced the link
with a file of its own, the repository's file kept the old values, and the next edits went where the owner did not
look."""

import stat
import tomllib
from pathlib import Path

from dotagent.config_edit import set_setting
from dotagent.secrets import parse_env_file, write_env_value


def test_a_linked_config_is_changed_where_it_leads_and_stays_a_link(tmp_path: Path) -> None:
    kept: Path = tmp_path / "dotfiles" / "dot" / "config.toml"
    kept.parent.mkdir(parents=True)
    kept.write_text('[snapshots]\nsound = "tink"\n', encoding="utf-8")
    home: Path = tmp_path / "home"
    home.mkdir()
    link: Path = home / "config.toml"
    link.symlink_to(kept)
    set_setting(link, "snapshots", "sound", "pop")
    assert link.is_symlink() and link.resolve() == kept.resolve()
    assert tomllib.loads(kept.read_text(encoding="utf-8"))["snapshots"]["sound"] == "pop"
    assert [child.name for child in home.iterdir()] == ["config.toml"]


def test_a_linked_env_file_is_changed_where_it_leads_stays_a_link_and_the_owners_alone(tmp_path: Path) -> None:
    kept: Path = tmp_path / "dotfiles" / "dot.env"
    kept.parent.mkdir()
    kept.write_text("FIRST=one\n", encoding="utf-8")
    kept.chmod(0o600)
    home: Path = tmp_path / "home"
    home.mkdir()
    link: Path = home / ".env"
    link.symlink_to(kept)
    write_env_value(link, "SECOND", "two")
    assert link.is_symlink() and link.resolve() == kept.resolve()
    assert parse_env_file(kept.read_text(encoding="utf-8")) == {"FIRST": "one", "SECOND": "two"}
    assert stat.S_IMODE(kept.stat().st_mode) == 0o600
