"""What the desktop session remembers per conversation thread: the application a thread works in, the elements it was
given refs for (a ref stays valid while its element lives) and what the model was last shown of each view.

Everything here is immutable: updates return new values.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace

from dotagent.desktop.types import AXElement, Node, Snapshot

KNOWN_ELEMENTS_CAP: int = 5000
FIRST_REF: int = 1


@dataclass(frozen=True, slots=True)
class AppMemory:
    """One application as one thread knows it: ``known`` holds every element that was given a ref (merged over all
    walks) and ``rendered`` the line text of the last complete view of each kind."""

    known: Snapshot | None
    rendered: Mapping[str, Mapping[int, str]]


@dataclass(frozen=True, slots=True)
class ThreadDesktop:
    """One conversation thread: its target application (``None`` before the first use) and the shared ref counter."""

    pid: int | None
    next_ref: int
    apps: Mapping[int, AppMemory]


def fresh_thread() -> ThreadDesktop:
    return ThreadDesktop(pid=None, next_ref=FIRST_REF, apps={})


def memory_of(thread: ThreadDesktop, pid: int) -> AppMemory:
    found: AppMemory | None = thread.apps.get(pid)
    return found if found is not None else AppMemory(known=None, rendered={})


def targeting(thread: ThreadDesktop, pid: int) -> ThreadDesktop:
    return replace(thread, pid=pid)


def known_elements(memory: AppMemory) -> Mapping[int, AXElement]:
    return {} if memory.known is None else memory.known.elements


def known_nodes(memory: AppMemory) -> Mapping[int, Node]:
    return {} if memory.known is None else {node.ref: node for node in memory.known.nodes}


def merge_known(previous: Snapshot | None, walk: Snapshot, cap: int) -> Snapshot:
    """``walk`` plus every element ``previous`` knew that the walk did not reach; beyond ``cap`` the oldest refs go."""
    merged: Snapshot = walk
    if previous is not None:
        reached: frozenset[int] = frozenset(node.ref for node in walk.nodes)
        older: tuple[Node, ...] = tuple(node for node in previous.nodes if node.ref not in reached)
        merged = replace(walk, nodes=(*walk.nodes, *older), elements={**previous.elements, **walk.elements})
    if len(merged.elements) <= cap:
        return merged
    keep: frozenset[int] = frozenset(sorted(merged.elements)[-cap:])
    return replace(
        merged,
        nodes=tuple(node for node in merged.nodes if node.ref in keep),
        elements={ref: element for ref, element in merged.elements.items() if ref in keep},
    )


def after_walk(
    thread: ThreadDesktop, pid: int, walk: Snapshot, view: str, baseline: Mapping[int, str] | None
) -> ThreadDesktop:
    """The thread after a walk of ``pid`` that produced ``view`` (``baseline`` ``None``: the stored one stays)."""
    memory: AppMemory = memory_of(thread, pid)
    rendered: Mapping[str, Mapping[int, str]] = (
        memory.rendered if baseline is None else {**memory.rendered, view: baseline}
    )
    updated: AppMemory = AppMemory(known=merge_known(memory.known, walk, KNOWN_ELEMENTS_CAP), rendered=rendered)
    return ThreadDesktop(pid=pid, next_ref=walk.next_ref, apps={**thread.apps, pid: updated})
