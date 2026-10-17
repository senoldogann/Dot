"""``dotx models`` ends with 1 when no provider answers, so a script that runs it can tell; one provider that answers is
enough for 0, as a provider without a key is the usual state. The real command runs in a child process with a throw-away
home whose providers all point at a closed port, and OpenCode's at a program that does not exist (``conftest.py``); the
keys are not real ones, set in the environment so that the Keychain is never read, and no service is asked (item 177)."""

import os
import subprocess
import sys
from pathlib import Path

CLOSED: str = "http://127.0.0.1:9"
PROVIDERS: tuple[str, ...] = ("ollama", "openai", "opencode_go", "anthropic", "openrouter")
KEYS: tuple[str, ...] = (
    "OLLAMA_API_KEY",
    "OPENAI_API_KEY",
    "OPENAI_ORG_ID",
    "OPENAI_PROJECT_ID",
    "OPENCODE_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENROUTER_API_KEY",
)
_TIME_LIMIT_S: float = 90.0


def test_dotx_models_ends_with_1_when_no_provider_answers(tmp_path: Path) -> None:
    home: Path = tmp_path / "home"
    home.mkdir()
    sections: str = "".join(f'[providers.{name}]\nbase_url = "{CLOSED}"\nattempts = 1\n\n' for name in PROVIDERS)
    (home / "config.toml").write_text(sections, encoding="utf-8")
    env: dict[str, str] = {
        **os.environ,
        "DOT_HOME": str(home),
        "DOT_LOG_DIR": str(tmp_path / "logs"),
        **dict.fromkeys(KEYS, "not-a-real-key-177"),
    }
    listed: subprocess.CompletedProcess[str] = subprocess.run(
        [sys.executable, "-m", "dotagent", "models"],
        capture_output=True,
        text=True,
        env=env,
        timeout=_TIME_LIMIT_S,
        check=False,
    )
    assert listed.stdout.count(": unavailable - ") == len(PROVIDERS) + 1, listed.stdout + listed.stderr
    assert "opencode: unavailable - OpenCode is not installed" in listed.stdout
    assert listed.returncode == 1, listed.stdout + listed.stderr
