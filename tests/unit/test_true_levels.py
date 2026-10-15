"""The reasoning levels of OpenCode Go's catalog are the ones the gateway honours (item 258): DeepSeek's models offer
none, since the chat route thought the same at every label, but for V4.1 Flash, whose levels the owner chose to keep
(item 290)."""

from dotagent.llm.opencode_go import CATALOG


def test_deepseek_offers_no_reasoning_level_but_on_v4_1_flash() -> None:
    deepseek: dict[str, tuple[str, ...]] = {
        name: model.traits.efforts for name, model in CATALOG.items() if "deepseek" in name
    }
    assert deepseek == {
        "deepseek-v4.1-flash": ("low", "high", "max"),
        "deepseek-v4-flash": (),
        "deepseek-v4-pro": (),
        "deepseek-v4-flash-vision-exp": (),
    }


def test_models_whose_level_is_honoured_keep_theirs() -> None:
    assert CATALOG["glm-5.2"].traits.efforts == ("high", "max")
    assert CATALOG["grok-4.7"].traits.efforts == ("low", "medium", "high", "xhigh")
