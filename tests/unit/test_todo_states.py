"""A thing of Dot's list moves only from where its writer read it (item 1048): one let go while its try was weighed
stays let go, and one let go before it was started is not started."""

from pathlib import Path

from dotagent.store.db import Store
from dotagent.store.models import TodoRecord


async def test_a_thing_let_go_meanwhile_stays_let_go(tmp_path: Path) -> None:
    store: Store = await Store.open(tmp_path / "dot.db", tmp_path / "images")
    try:
        todo: TodoRecord = await store.add_todo("Water the plants", "water the plants", "", "test", None, False)
        doing: TodoRecord | None = await store.set_todo(todo.id, "open", "doing", 0, None, "")
        assert doing is not None and doing.status == "doing"
        # Its run lets it go as the owner said, while the end of its try is weighed.
        assert await store.set_todo(todo.id, "doing", "dropped", 0, None, "Let go by its run") is not None
        assert await store.set_todo(todo.id, "doing", "open", 1, None, "Tried") is None
        assert await store.set_todo(todo.id, "open", "doing", 1, None, "") is None
        kept: TodoRecord = await store.get_todo(todo.id)
        assert (kept.status, kept.note) == ("dropped", "Let go by its run")
    finally:
        await store.close()
