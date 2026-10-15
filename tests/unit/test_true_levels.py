"""The reasoning levels of OpenCode Go's catalog are the ones the gateway honours (item 258): DeepSeek's models offer
none, since the chat route thinks the same at every label, so the menu never offers a choice that changes nothing."""

from dotagent.llm.opencode_go import CATALOG


def test_deepseek_offers_no_reasoning_level() -> None:
    deepseek: dict[str, tuple[str, ...]] = {
        name: model.traits.efforts for name, model in CATALOG.items() if "deepseek" in name
    }
    assert len(deepseek) == 4
    assert all(efforts == () for efforts in deepseek.values()), deepseek


def test_models_whose_level_is_honoured_keep_theirs() -> None:
    assert CATALOG["glm-5.2"].traits.efforts == ("high", "max")
    assert CATALOG["grok-4.7"].traits.efforts == ("low", "medium", "high", "xhigh")
