"""The choices for one message (item 251): Ultrathink asks the model for its Max level, Ultracode for its Extra high
level and has the run told to split its work across delegated runs; the instructions say so only to a run that can
delegate, and the store keeps the choice with a run, a queued message and a campaign."""

from pathlib import Path

from dotagent.agent.prompt import build_system_prompt, collect_prompt_inputs
from dotagent.llm.types import ONE_MESSAGE_LEVELS, splits_work
from dotagent.store.db import Store
from dotagent.store.models import NO_PICKS, CampaignRecord, QueuedMessage, RunRecord, ThreadRecord
from dotagent.tools.delegate import DELEGATE_TOOL

OPUS: str = "anthropic:claude-opus-5-5"
SPLIT: str = "Split: the user asked for the independent parts of this goal to be done at the same time"


def _prompt(split: bool, tool_names: list[str]) -> str:
    return build_system_prompt(
        collect_prompt_inputs(
            "task",
            "cli",
            "aggressive",
            "ask",
            "wait",
            False,
            split,
            Path("/tmp/work"),
            [],
            tool_names,
            [],
            0,
            None,
            [],
            None,
            (),
            (),
            None,
        )
    )


def test_ultrathink_asks_for_max_and_ultracode_for_extra_high_with_the_work_split() -> None:
    assert ONE_MESSAGE_LEVELS == {"ultrathink": "max", "ultracode": "xhigh"}
    assert (splits_work("ultracode"), splits_work("ultrathink"), splits_work(None)) == (True, False, False)


def test_only_a_run_that_can_delegate_is_told_to_split() -> None:
    assert SPLIT in _prompt(True, [DELEGATE_TOOL])
    # A delegated run has no delegate tool: it cannot split, so it is not told to.
    assert SPLIT not in _prompt(True, [])
    assert SPLIT not in _prompt(False, [DELEGATE_TOOL])


async def test_the_store_keeps_the_choice_with_a_run_a_queued_message_and_a_campaign(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        thread: ThreadRecord = await store.create_thread("Compare the reports", "test")
        run: RunRecord = await store.create_run(
            thread.id,
            "Compare the reports",
            "task",
            OPUS,
            None,
            "test",
            "xhigh",
            "standard",
            "ultracode",
            "ask",
            False,
            None,
            None,
            None,
        )
        queued: QueuedMessage = await store.add_queued(
            thread.id,
            "And the third one",
            [],
            "task",
            None,
            "high",
            "standard",
            "ultrathink",
            NO_PICKS,
            None,
            None,
            None,
            "test",
        )
        campaign: CampaignRecord = await store.create_campaign(
            "Compare the reports",
            "Compare the reports",
            thread.id,
            OPUS,
            "xhigh",
            "standard",
            "ultracode",
            "ask",
            [],
            "own",
        )
        assert (await store.get_run(run.id)).once == "ultracode"
        assert [(message.id, message.once, message.effort) for message in await store.list_queued(thread.id)] == [
            (queued.id, "ultrathink", "high")
        ]
        assert (await store.get_campaign(campaign.id)).once == "ultracode"
    finally:
        await store.close()
