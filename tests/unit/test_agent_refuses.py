"""What an agent never does (item 875): the store keeps it through a change that does not name it, and every
template carries its own (the prompt's part is in test_agents.py)."""

from pathlib import Path

from dotagent.agent.prompt import refusal_lines
from dotagent.agent.templates import TEMPLATES, agent_file
from dotagent.store.db import Store
from dotagent.store.models import AgentRecord


def test_refusal_lines_drop_bullets_and_blank_lines() -> None:
    assert refusal_lines("* one\n\n  - two  \n•three\n-\n") == ["one", "two", "three"]


async def test_the_store_keeps_what_an_agent_never_does_until_a_change_names_it(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")
    try:
        made: AgentRecord = await store.create_agent(
            "Scout", "", "", "", None, None, ("web_search",), None, None, "Log in anywhere"
        )
        assert (await store.get_agent(made.id)).refuses == "Log in anywhere"
        # A change that does not name it (trimming its tools, an older client) keeps it.
        kept = await store.update_agent(made.id, "Scout", "", "", "", None, None, ("web_search",), None)
        assert kept.refuses == "Log in anywhere"
        cleared = await store.update_agent(made.id, "Scout", "", "", "", None, None, ("web_search",), None, "")
        assert cleared.refuses == ""
    finally:
        await store.close()


def test_every_template_says_what_its_agent_never_does_and_its_file_carries_it() -> None:
    assert all(2 <= len(template.refuses) <= 5 for template in TEMPLATES)
    record: AgentRecord = AgentRecord(
        id="agt_x",
        name="Scout",
        role="",
        instructions="",
        model="",
        effort=None,
        approvals=None,
        tools=("web_search",),
        created_at=0.0,
        updated_at=0.0,
        made_by=None,
        look=None,
        paused_at=None,
        refuses="Log in anywhere",
    )
    assert agent_file(record, []).refuses == "Log in anywhere"
