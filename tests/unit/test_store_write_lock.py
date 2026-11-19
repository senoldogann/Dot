"""Every write of the store runs under its write lock (item 1236). The connection is one for every writer, so a method
that commits without the lock commits whatever another writer's unit has done so far: three did (the campaigns' guarded
updates, a campaign achieved while active, the harness steps closed at start), found by the code review of
2026-11-19. A method that commits must hold the lock itself, or be a helper whose every caller holds it."""

import ast
from collections.abc import Iterator
from pathlib import Path

import dotagent.store.db as db

_LOCK: str = "_writing"


def _own_calls(method: ast.AsyncFunctionDef) -> Iterator[str]:
    """The calls a method makes itself, not those of the functions defined in it (a unit's statements)."""
    stack: list[ast.AST] = list(method.body)
    while stack:
        node: ast.AST = stack.pop()
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            continue
        if isinstance(node, ast.Call):
            yield ast.unparse(node.func)
        stack.extend(ast.iter_child_nodes(node))


def test_every_method_that_commits_holds_the_write_lock_or_is_called_only_by_one_that_does() -> None:
    tree: ast.Module = ast.parse(Path(db.__file__).read_text(encoding="utf-8"))
    store: ast.ClassDef = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Store")
    methods: dict[str, ast.AsyncFunctionDef] = {
        node.name: node for node in store.body if isinstance(node, ast.AsyncFunctionDef)
    }
    locked: set[str] = {
        name for name, node in methods.items() if any(ast.unparse(mark) == _LOCK for mark in node.decorator_list)
    }
    calls: dict[str, set[str]] = {name: set(_own_calls(node)) for name, node in methods.items()}
    unlocked: list[str] = []
    for name in methods:
        if "self._db.commit" not in calls[name] or name in locked:
            continue
        callers: set[str] = {other for other, made in calls.items() if f"self.{name}" in made}
        if not callers or not callers <= locked:
            unlocked.append(name)
    assert unlocked == []
    # The lock is a plain ``asyncio.Lock``: a method that holds it and calls another that takes it waits for ever.
    nested: list[str] = [name for name in locked if any(f"self.{other}" in calls[name] for other in locked)]
    assert nested == []
