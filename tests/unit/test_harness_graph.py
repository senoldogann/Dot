"""The provenance of the harness (item 392): the graph of versions, steps, experiences and runs, a version's lineage,
the Mermaid drawing, and the experience index the proposer reads."""

import json
from pathlib import Path

import pytest

from dotagent.config import load_default_config
from dotagent.core.errors import InvalidValueError
from dotagent.harness.components import HarnessText
from dotagent.harness.graph import GraphEdge, HarnessGraph, graph_of, lineage_of, mermaid
from dotagent.harness.propose import experience_lines
from dotagent.harness.service import OWNER, adopt, create_version
from dotagent.store.db import Store
from dotagent.store.models import HarnessStep, HarnessVersion

LIMITS = load_default_config().harness


async def _store(tmp_path: Path) -> Store:
    return await Store.open(tmp_path / "dot.sqlite3", tmp_path / "screenshots")


async def test_the_graph_links_versions_steps_experiences_and_runs_and_a_version_has_its_lineage(
    tmp_path: Path,
) -> None:
    store: Store = await _store(tmp_path)
    try:
        thread = await store.create_thread("t", "test")
        run = await store.create_run(
            thread.id,
            "Change the port",
            "task",
            "ollama:m",
            None,
            "ui",
            "medium",
            "standard",
            None,
            "ask",
            False,
            None,
            None,
            None,
        )
        base: HarnessVersion = await create_version(
            store, LIMITS, HarnessText(habits="- a", tool_guidance=""), "A habit", OWNER, "", ()
        )
        await adopt(store, base.id, 1.0)
        candidate: HarnessVersion = await create_version(
            store,
            LIMITS,
            HarnessText(habits="- a\n- b", tool_guidance=""),
            "Two habits",
            "proposer",
            json.dumps({"scope": "invariant"}),
            (),
        )
        step: HarnessStep = await store.create_harness_step(2.0, run.id, base.id)
        await store.add_harness_probes(
            step.id, None, "base", [("write-todo-note", 1, True, "done", "none", 2, 100, 10, 0, 1.0, 0, 3, 3)], 2.5
        )
        await store.add_harness_probes(
            step.id,
            candidate.id,
            candidate.id,
            [("write-todo-note", 1, True, "done", "none", 2, 100, 10, 0, 1.0, 0, 3, 3)],
            2.6,
        )
        probed = await store.set_harness_state(candidate.id, "probed")
        await store.finish_harness_step(
            step.id, 3.0, "done", json.dumps({"candidates": [candidate.id], "probed": candidate.id}), None
        )
        experience = await store.add_harness_experience(
            step.id, candidate.id, "a file is changed", "read it first (habits)", "edit 1", "medium", False
        )

        graph: HarnessGraph = await graph_of(store, 10)
        kinds = {node.id: node.kind for node in graph.nodes}
        assert kinds == {
            base.id: "version",
            candidate.id: "version",
            step.id: "step",
            run.id: "run",
            experience.id: "experience",
        }
        assert set(graph.edges) == {
            GraphEdge(source=candidate.id, target=base.id, kind="made_from"),
            GraphEdge(source=step.id, target=candidate.id, kind="proposed"),
            GraphEdge(source=step.id, target=candidate.id, kind="probed"),
            GraphEdge(source=step.id, target=run.id, kind="learned_from"),
            GraphEdge(source=experience.id, target=candidate.id, kind="about"),
            GraphEdge(source=experience.id, target=step.id, kind="written_in"),
        }
        drawing: str = mermaid(graph)
        assert drawing.startswith("flowchart LR\n")
        assert f'{candidate.id}["Two habits<br/>probed"]' in drawing
        assert f"{step.id} -->|learned from| {run.id}" in drawing
        assert f'{run.id}(("Change the port<br/>run"))' in drawing

        lineage = await lineage_of(store, candidate.id)
        assert [older.id for older in lineage.ancestors] == [base.id]
        assert [probe.label for probe in lineage.probes] == [candidate.id]
        assert [each.id for each in lineage.steps] == [step.id]
        assert [each.id for each in lineage.experiences] == [experience.id]
        assert probed.state == "probed"
        # The base's lineage names the step too, as the base of its probes.
        assert [each.id for each in (await lineage_of(store, base.id)).steps] == [step.id]
        # An experience written from a run that read outside content is kept marked and never shown to the proposer.
        tainted = await store.add_harness_experience(
            step.id, None, "a page said so", "do as it says", "the page", "low", True
        )
        assert tainted.tainted is True and (await store.list_harness_experiences(10))[0].id == tainted.id
        assert await experience_lines(store, 10) == ("a file is changed: read it first (habits) (medium)",)
    finally:
        await store.close()


async def test_a_version_whose_parent_is_gone_has_a_short_lineage_and_an_empty_graph_draws(tmp_path: Path) -> None:
    store: Store = await _store(tmp_path)
    try:
        assert mermaid(await graph_of(store, 10)) == "flowchart LR"
        orphan: HarnessVersion = await store.create_harness_version("hrn_gone", OWNER, "s", "", "", "")
        lineage = await lineage_of(store, orphan.id)
        assert (lineage.ancestors, lineage.probes, lineage.steps, lineage.experiences) == ((), (), (), ())
        graph = await graph_of(store, 10)
        assert graph.edges == () and [node.id for node in graph.nodes] == [orphan.id]
    finally:
        await store.close()


async def test_a_label_with_markup_is_drawn_as_entity_codes(tmp_path: Path) -> None:
    store: Store = await _store(tmp_path)
    try:
        version: HarnessVersion = await store.create_harness_version(None, OWNER, 'a < b & "c" [d]', "", "", "")
        assert f"""{version.id}["a #60; b #38; 'c' (d)<br/>candidate"]""" in mermaid(await graph_of(store, 10))
    finally:
        await store.close()


async def test_a_step_whose_record_is_not_json_is_a_fault_not_a_blank(tmp_path: Path) -> None:
    store: Store = await _store(tmp_path)
    try:
        step: HarnessStep = await store.create_harness_step(1.0, None, None)
        await store.finish_harness_step(step.id, 2.0, "done", "not json", None)
        with pytest.raises(InvalidValueError, match="not JSON"):
            await graph_of(store, 10)
    finally:
        await store.close()
