"""The rows the checks page sorts: each provider's models row carries its own provider's name, and the Antigravity login
has a row of its own that says whether the file a run would need is there (item 1228)."""

from pathlib import Path

import pytest

from dotagent import doctor
from dotagent.codemode.sandbox import Probe
from dotagent.config import AntigravityConfig, load_default_config
from dotagent.doctor import Served, _antigravity_login_row, _openai_api_row


def test_a_models_row_is_named_for_its_own_provider() -> None:
    served = Served(names=frozenset({"a", "b"}), problem="")
    assert _openai_api_row(served, True).name == "openai api"
    assert _openai_api_row(served, True, "google api").name == "google api"


def test_the_antigravity_row_says_whether_the_login_is_there(tmp_path: Path) -> None:
    login = tmp_path / "acp_token.json"
    login.write_text("{}")
    config = AntigravityConfig(
        base_url="https://daily-cloudcode-pa.googleapis.com",
        credentials_file=str(login),
        project="p",
        user_agent="antigravity",
        connect_timeout_s=10.0,
        read_timeout_s=600.0,
        attempts=6,
        max_concurrent=4,
    )
    row = _antigravity_login_row(config, True)
    assert row.name == "antigravity login" and row.ok

    gone = _antigravity_login_row(config.model_copy(update={"credentials_file": str(tmp_path / "nope" / "*")}), True)
    assert gone.name == "antigravity login" and not gone.ok and "sign in to Antigravity once" in gone.detail


async def test_the_code_mode_row_says_whether_the_sandbox_holds_and_what_it_is_when_it_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Item 1503: the row is the probe that ``code_run`` goes by; where it fails, no code runs, and the row says why."""
    config = load_default_config()
    answers: list[Probe] = [
        Probe(ok=True, why="the sandbox holds", python="/py/bin/python3.13", macos="26.6.2"),
        Probe(ok=False, why="/usr/bin/sandbox-exec is not there", python="/py/bin/python3.13", macos="26.6.2"),
    ]

    async def ready(*_arguments: object) -> Probe:
        return answers.pop(0)

    monkeypatch.setattr(doctor, "code_mode_ready", ready)
    holds = await doctor._code_mode_row(config)
    assert (holds.name, holds.ok, holds.required) == ("code mode", True, False)
    assert "holds" in holds.detail and "macOS 26.6.2" in holds.detail
    broken = await doctor._code_mode_row(config)
    assert (broken.ok, broken.required) == (False, False)
    assert broken.detail.startswith("code_run runs nothing here: /usr/bin/sandbox-exec is not there")
    off = await doctor._code_mode_row(
        config.model_copy(update={"code": config.code.model_copy(update={"enabled": False})})
    )
    assert (off.ok, off.detail) == (True, "off ([code] enabled = false)")
