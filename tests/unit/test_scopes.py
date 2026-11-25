"""``dotx scope`` and the store it writes (item 1888): a bug bounty program's hosts in one file, filled in one command,
so no run and no owner hand-edits a toml block, and the gate of `recon` and `security_scan` reads it on every call."""

from pathlib import Path

import pytest

from dotagent import scopes
from dotagent.cli_commands import (
    COMMAND_ADAPTER,
    ScopeAddCommand,
    ScopeCheckCommand,
    ScopeListCommand,
    ScopeRemoveCommand,
)
from dotagent.cli_scope import dispatch
from dotagent.config import ReconAuthority, ReconConfig, ReconScope, load_default_config


def test_a_scope_written_by_one_command_is_read_back_with_its_rules(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(scopes, "scopes_path", lambda: tmp_path / "scopes.toml")
    entry, replaced = scopes.add_scope(
        "Bugcrowd · Klaviyo",
        ("a.klaviyo.com", "klaviyo.com"),
        url="https://bugcrowd.com/engagements/klaviyo-og",
        note="no scanners; 5 req/s",
    )
    assert not replaced
    held = scopes.load_scopes()
    assert len(held) == 1
    assert held[0] == entry
    assert held[0].note == "no scanners; 5 req/s"
    assert held[0].url == "https://bugcrowd.com/engagements/klaviyo-og"


def test_a_second_add_of_the_same_program_replaces_it_and_keeps_the_others(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(scopes, "scopes_path", lambda: tmp_path / "scopes.toml")
    scopes.add_scope("Bugcrowd · Klaviyo", ("klaviyo.com",))
    scopes.add_scope("YesWeHack · ATG", ("atg.se",))
    _entry, replaced = scopes.add_scope("bugcrowd · klaviyo", ("a.klaviyo.com",))
    assert replaced
    held = {scope.program: scope for scope in scopes.load_scopes()}
    assert set(held) == {"bugcrowd · klaviyo", "YesWeHack · ATG"}
    assert held["bugcrowd · klaviyo"].hosts == ("a.klaviyo.com",)


def test_remove_takes_one_program_and_says_whether_it_was_there(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(scopes, "scopes_path", lambda: tmp_path / "scopes.toml")
    scopes.add_scope("Bugcrowd · Klaviyo", ("klaviyo.com",))
    assert scopes.remove_scope("Bugcrowd · Klaviyo")
    assert not scopes.remove_scope("Bugcrowd · Klaviyo")
    assert scopes.load_scopes() == ()


def test_a_scope_without_hosts_is_still_kept_for_its_rules(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(scopes, "scopes_path", lambda: tmp_path / "scopes.toml")
    scopes.add_scope("YesWeHack · ATG", (), url="https://yeswehack.com/programs/atg", note="no DoS")
    held = scopes.load_scopes()
    assert held[0].hosts == ()
    assert held[0].note == "no DoS"


def test_the_gate_reads_the_store_a_name_covers_its_subdomains_and_the_own_list_comes_first(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(scopes, "scopes_path", lambda: tmp_path / "scopes.toml")
    scopes.add_scope("Bugcrowd · Klaviyo", ("klaviyo.com",), note="5 req/s")
    recon = ReconConfig(owned_hosts=("example.com",))
    assert scopes.authorising("api.klaviyo.com", recon) == ReconAuthority(program="Bugcrowd · Klaviyo", note="5 req/s")
    assert scopes.authorising("klaviyo.com", recon) == ReconAuthority(program="Bugcrowd · Klaviyo", note="5 req/s")
    assert scopes.authorising("evilklaviyo.com", recon) is None
    assert scopes.authorising("example.com", recon) == ReconAuthority(program=None, note="")
    assert scopes.authorising("elsewhere.test", recon) is None


def test_a_scope_added_while_a_run_is_working_holds_on_the_next_call(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(scopes, "scopes_path", lambda: tmp_path / "scopes.toml")
    recon = ReconConfig()
    assert scopes.authorising("atg.se", recon) is None
    scopes.add_scope("YesWeHack · ATG", ("atg.se",))
    assert scopes.authorising("www.atg.se", recon) == ReconAuthority(program="YesWeHack · ATG", note="")


def test_the_config_file_scope_still_answers_first(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(scopes, "scopes_path", lambda: tmp_path / "scopes.toml")
    scopes.add_scope("From the store", ("acme.com",))
    recon = ReconConfig(scopes=(ReconScope(program="From the config", hosts=("acme.com",)),))
    assert scopes.authorising("acme.com", recon) == ReconAuthority(program="From the config", note="")


async def test_the_command_writes_and_lists_what_was_typed(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(scopes, "scopes_path", lambda: tmp_path / "scopes.toml")
    added = await dispatch(
        ScopeAddCommand(
            name="scope-add",
            program="Bugcrowd · Klaviyo",
            hosts=["a.klaviyo.com, mcp.klaviyo.com"],
            url="https://bugcrowd.com/engagements/klaviyo-og",
            note="no scanners",
        )
    )
    assert added == 0
    assert "mcp.klaviyo.com" in capsys.readouterr().out
    assert await dispatch(ScopeListCommand(name="scope-list")) == 0
    listed = capsys.readouterr().out
    assert "Bugcrowd · Klaviyo" in listed and "a.klaviyo.com" in listed


async def test_check_answers_who_covers_a_url_and_who_does_not(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(scopes, "scopes_path", lambda: tmp_path / "scopes.toml")
    scopes.add_scope("Bugcrowd · Klaviyo", ("klaviyo.com",), note="5 req/s")
    monkeypatch.setattr("dotagent.cli_scope.load_user_config", load_default_config)
    assert await dispatch(ScopeCheckCommand(name="scope-check", target="https://api.klaviyo.com/v1/x")) == 0
    assert "Bugcrowd · Klaviyo" in capsys.readouterr().out
    assert await dispatch(ScopeCheckCommand(name="scope-check", target="https://example.com")) == 1
    assert "no scope covers it" in capsys.readouterr().out


async def test_removing_something_that_is_not_kept_says_so(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(scopes, "scopes_path", lambda: tmp_path / "scopes.toml")
    assert await dispatch(ScopeRemoveCommand(name="scope-remove", program="Nobody")) == 1
    assert "No scope of 'Nobody'" in capsys.readouterr().out


@pytest.mark.parametrize(
    "arguments",
    [
        ["scope", "list"],
        ["scope", "add", "--program", "A · B", "--hosts", "a.com,b.com", "--url", "https://x.test", "--note", "slow"],
        ["scope", "check", "https://a.com/x"],
    ],
)
def test_the_cli_reads_every_scope_command(arguments: list[str]) -> None:
    from dotagent.cli import build_parser, parse_command

    command = parse_command(build_parser(), arguments)
    assert command.name.startswith("scope-")


def test_every_scope_command_has_a_family_to_run_in() -> None:
    from dotagent.cli import _FAMILIES

    for name in ("scope-list", "scope-add", "scope-remove", "scope-check"):
        assert _FAMILIES[name] == "dotagent.cli_scope"


def test_the_commands_are_typed() -> None:
    assert isinstance(COMMAND_ADAPTER.validate_python({"name": "scope-list"}), ScopeListCommand)


def test_a_broken_scopes_file_is_named_not_skipped(tmp_path: Path, monkeypatch) -> None:
    """A file that is not TOML, or a scope in it that is not valid, raises with the file named: no scope is dropped
    without a word."""
    path: Path = tmp_path / "scopes.toml"
    monkeypatch.setattr(scopes, "scopes_path", lambda: path)
    path.write_text("[[scopes]\nprogram = ", encoding="utf-8")
    with pytest.raises(scopes.ScopesFileError, match="not valid TOML"):
        scopes.load_scopes()
    path.write_text('[[scopes]]\nprogram = "Acme"\nhosts = ["*.acme.com"]\n', encoding="utf-8")
    with pytest.raises(scopes.ScopesFileError, match="scope 1 is not a valid scope"):
        scopes.load_scopes()
    path.write_text('[[scopes]]\nhosts = ["acme.com"]\n', encoding="utf-8")
    with pytest.raises(scopes.ScopesFileError, match="scope 1 is not a valid scope"):
        scopes.load_scopes()
    path.write_bytes(b'[[scopes]]\nprogram = "\xff\xfe"\n')
    with pytest.raises(scopes.ScopesFileError, match="cannot be read as UTF-8"):
        scopes.load_scopes()
    path.unlink()
    path.mkdir()
    with pytest.raises(scopes.ScopesFileError, match="cannot be read"):
        scopes.load_scopes()


async def test_an_add_with_a_bad_host_says_so_in_one_line(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(scopes, "scopes_path", lambda: tmp_path / "scopes.toml")
    code: int = await dispatch(
        ScopeAddCommand(name="scope-add", program="Acme", hosts=("https://acme.com/x",), url="", note="")
    )
    out: str = capsys.readouterr().out
    assert code == 1
    assert out.startswith("The scope of 'Acme' was not written:")
    assert "Traceback" not in out
    assert not (tmp_path / "scopes.toml").exists()
