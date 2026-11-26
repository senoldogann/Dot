"""The models of the pre-push review chosen through the API (item 1663): Settings › Models › Code review reads and
writes ``[code_review]`` in config.toml, all of it or none, and `dotx review plan` reads the same; a phone's key reads
and changes them too (item 1770), their efforts included. No model service is asked: the router's answer about a model
is a stand-in."""

from pathlib import Path

import httpx
import pytest
from tests.smoke.test_phone_limits import TOKEN, _phone_key, _served

from dotagent.code_review import plan_text, review_plan
from dotagent.config import load_user_config
from dotagent.core.paths import config_path, ensure_layout
from dotagent.llm.types import ModelInfo

pytestmark = pytest.mark.asyncio


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    ensure_layout()
    return home


_CHOSEN: dict[str, object] = {
    "model": "opencode_go:deepseek-v4.1-flash",
    "fallback": ["ollama:qwen3:cloud"],
    "small_model": "ollama:gemma4:cloud",
    "small_max_lines": 80,
    "max_minutes": 10,
}


async def test_the_owner_chooses_the_review_models_on_the_mac_or_the_phone(
    own_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with _served(own_home) as (client, _, runtime):

        async def able(ref: object) -> ModelInfo:
            return ModelInfo(
                id=str(ref),
                vision=False,
                tools=True,
                thinking=False,
                context_length=None,
                own_context_length=None,
                window_source=None,
                efforts=(),
                free=False,
            )

        monkeypatch.setattr(runtime.router, "model_info", able)
        mac: dict[str, str] = {"Authorization": f"Bearer {TOKEN}"}
        phone: dict[str, str] = {"Authorization": f"Bearer {await _phone_key(client)}"}
        first: httpx.Response = await client.get("/code-review", headers=phone)
        assert first.status_code == 200, first.text
        assert (first.json()["model"], first.json()["fallback"]) == (
            "claude_code:opus",
            ["opencode_go:deepseek-v4.1-flash"],
        )
        # From the phone (item 1770): the models and both efforts, which a body without them keeps.
        phoned: httpx.Response = await client.put(
            "/code-review", json={**_CHOSEN, "effort": "high", "fallback_effort": "low"}, headers=phone
        )
        assert phoned.status_code == 200, phoned.text
        assert (phoned.json()["effort"], phoned.json()["fallback_effort"]) == ("high", "low")
        bad: httpx.Response = await client.put("/code-review", json={**_CHOSEN, "effort": "loud"}, headers=phone)
        assert bad.status_code == 422, bad.text
        chosen: httpx.Response = await client.put("/code-review", json=_CHOSEN, headers=mac)
        assert chosen.status_code == 200, chosen.text
        assert {key: chosen.json()[key] for key in _CHOSEN} == _CHOSEN
        assert "[code_review]" in config_path().read_text(encoding="utf-8")
        assert (chosen.json()["effort"], chosen.json()["fallback_effort"]) == ("high", "low"), "a body without efforts"
        back: httpx.Response = await client.put(
            "/code-review", json={**_CHOSEN, "effort": "medium", "fallback_effort": "high"}, headers=mac
        )
        assert back.status_code == 200, back.text
        # What the hook reads through `dotx review plan` is what was chosen: a small change on the small model.
        plan: str = plan_text(review_plan(load_user_config().code_review, 50, False, False))
        assert (
            plan == "size small\nlimit 600\nroute small\ntry ollama:gemma4:cloud medium\ntry ollama:qwen3:cloud high\n"
        )
        twice: httpx.Response = await client.put(
            "/code-review", json={**_CHOSEN, "fallback": [_CHOSEN["model"]]}, headers=mac
        )
        assert twice.status_code == 422 and "twice" in twice.json()["message"], twice.text
        # One of Zen's free models answers only as OpenCode's own agent: it cannot run a review.
        agent: httpx.Response = await client.put("/code-review", json={**_CHOSEN, "model": "opencode:big"}, headers=mac)
        assert agent.status_code == 422, agent.text
        assert load_user_config().code_review.model == _CHOSEN["model"]
