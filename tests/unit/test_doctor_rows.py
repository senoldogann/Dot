"""The rows the checks page sorts: each provider's models row carries its own provider's name, and the Antigravity login
has a row of its own that says whether the file a run would need is there (item 1228)."""

from pathlib import Path

from dotagent.config import AntigravityConfig
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
