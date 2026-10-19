"""The provenance of the harness (item 392, docs/harness.md): what Dot changed about itself, when, and on what
evidence, as a graph the owner can read. Versions are made from versions; a step learns from a run, proposes
candidates, probes them and writes a probed version; an experience is about a version and written in a step. A
version's lineage is the same read from that version: its ancestors, its probes, the steps that touched it and the
experiences about it."""

from typing import Literal

from pydantic import JsonValue, TypeAdapter, ValidationError

from dotagent.core.errors import InvalidValueError, NotFoundError
from dotagent.core.text import plain_line
from dotagent.core.types import FrozenModel
from dotagent.harness.service import notes_of, step_facts
from dotagent.store.db import Store
from dotagent.store.models import HarnessExperience, HarnessProbe, HarnessStep, HarnessVersion

type NodeKind = Literal["version", "step", "experience", "run"]
type EdgeKind = Literal["made_from", "proposed", "probed", "composed_of", "learned_from", "about", "written_in"]
# How many characters of a label the graph shows.
_LABEL_CHARS: int = 60
# How far up a version's ancestors are followed: a chain longer than this is a cycle or a mistake.
_ANCESTORS: int = 100


class GraphNode(FrozenModel):
    id: str
    kind: NodeKind
    label: str
    state: str


class GraphEdge(FrozenModel):
    source: str
    target: str
    kind: EdgeKind


class HarnessGraph(FrozenModel):
    """The versions, the newest steps, the experiences and the runs they learned from, with what links them."""

    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]


class Lineage(FrozenModel):
    """One version with its ancestors (the nearest first), the attempts of the probes that played it, the steps that
    touched it (as base, candidate or probed version) and the experiences about it."""

    version: HarnessVersion
    ancestors: tuple[HarnessVersion, ...]
    probes: tuple[HarnessProbe, ...]
    steps: tuple[HarnessStep, ...]
    experiences: tuple[HarnessExperience, ...]


_IDS: TypeAdapter[list[str]] = TypeAdapter(list[str])


def _ids(value: JsonValue | None, where: dict[str, JsonValue]) -> list[str]:
    """The version ids a version's notes or a step's outcome name under a key, none when the key is absent; anything
    else is a fault."""
    if value is None:
        return []
    try:
        return _IDS.validate_python(value)
    except ValidationError as exc:
        raise InvalidValueError("not a list of version ids", where) from exc


async def graph_of(store: Store, steps: int) -> HarnessGraph:
    """The graph of every version, the newest ``steps`` steps and the newest ``4 * steps`` experiences; a run a step
    learned from is a node when the store still holds it."""
    versions: list[HarnessVersion] = await store.list_harness_versions()
    recent: list[HarnessStep] = await store.list_harness_steps(steps)
    experiences: list[HarnessExperience] = await store.list_harness_experiences(steps * 4)
    known: set[str] = {version.id for version in versions}
    nodes: list[GraphNode] = [
        GraphNode(id=version.id, kind="version", label=plain_line(version.summary, _LABEL_CHARS), state=version.state)
        for version in versions
    ]
    edges: list[GraphEdge] = [
        GraphEdge(source=version.id, target=version.parent_id, kind="made_from")
        for version in versions
        if version.parent_id is not None and version.parent_id in known
    ]
    for version in versions:
        for part in _ids(notes_of(version).get("composed_of"), {"version": version.id}):
            if part in known:
                edges.append(GraphEdge(source=version.id, target=part, kind="composed_of"))
    # A run's goal as a label; None for a run the store no longer holds, which is no node.
    runs: dict[str, str | None] = {}
    for step in recent:
        facts: dict[str, JsonValue] = step_facts(step)
        nodes.append(GraphNode(id=step.id, kind="step", label="step", state=step.state))
        for candidate in _ids(facts.get("candidates"), {"step": step.id}):
            if candidate in known:
                edges.append(GraphEdge(source=step.id, target=candidate, kind="proposed"))
        probed: object = facts.get("probed")
        if isinstance(probed, str) and probed in known:
            edges.append(GraphEdge(source=step.id, target=probed, kind="probed"))
        if step.from_run is not None:
            if step.from_run not in runs:
                try:
                    runs[step.from_run] = plain_line((await store.get_run(step.from_run)).goal, _LABEL_CHARS)
                except NotFoundError:
                    runs[step.from_run] = None
            if runs[step.from_run] is not None:
                edges.append(GraphEdge(source=step.id, target=step.from_run, kind="learned_from"))
    nodes.extend(
        GraphNode(id=run_id, kind="run", label=goal or run_id, state="run")
        for run_id, goal in runs.items()
        if goal is not None
    )
    step_ids: set[str] = {step.id for step in recent}
    for experience in experiences:
        nodes.append(
            GraphNode(
                id=experience.id,
                kind="experience",
                label=plain_line(experience.cue, _LABEL_CHARS),
                state=experience.confidence,
            )
        )
        if experience.version_id is not None and experience.version_id in known:
            edges.append(GraphEdge(source=experience.id, target=experience.version_id, kind="about"))
        if experience.step_id in step_ids:
            edges.append(GraphEdge(source=experience.id, target=experience.step_id, kind="written_in"))
    return HarnessGraph(nodes=tuple(nodes), edges=tuple(edges))


async def lineage_of(store: Store, version_id: str) -> Lineage:
    version: HarnessVersion = await store.get_harness_version(version_id)
    ancestors: list[HarnessVersion] = []
    parent: str | None = version.parent_id
    seen: set[str] = {version.id}
    while parent is not None and parent not in seen and len(ancestors) < _ANCESTORS:
        seen.add(parent)
        try:
            older: HarnessVersion = await store.get_harness_version(parent)
        except NotFoundError:
            break
        ancestors.append(older)
        parent = older.parent_id
    return Lineage(
        version=version,
        ancestors=tuple(ancestors),
        probes=tuple(await store.harness_probes_of_version(version_id)),
        steps=tuple(await store.harness_steps_of_version(version_id)),
        experiences=tuple(await store.harness_experiences_of_version(version_id)),
    )


def _mermaid_text(text: str) -> str:
    """``text`` as a label holds it: the characters Mermaid would read as markup or as the label's end as entity
    codes, and the shapes' own brackets as parentheses."""
    escaped: str = text.replace("&", "#38;").replace("<", "#60;").replace(">", "#62;")
    return escaped.replace('"', "'").replace("[", "(").replace("]", ")")


def mermaid(graph: HarnessGraph) -> str:
    """The graph as a Mermaid flowchart, left to right: versions as boxes, steps as rounded boxes, experiences as
    hexagons, runs as circles, each edge named for what it is."""
    shapes: dict[NodeKind, tuple[str, str]] = {
        "version": ("[", "]"),
        "step": ("(", ")"),
        "experience": ("{{", "}}"),
        "run": ("((", "))"),
    }
    lines: list[str] = ["flowchart LR"]
    for node in graph.nodes:
        opening, closing = shapes[node.kind]
        label: str = _mermaid_text(node.label) if node.label else node.id
        lines.append(f'    {node.id}{opening}"{label}<br/>{node.state}"{closing}')
    lines.extend(f"    {edge.source} -->|{edge.kind.replace('_', ' ')}| {edge.target}" for edge in graph.edges)
    return "\n".join(lines)
