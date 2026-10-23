"""A paste through the clipboard puts back what the owner had copied, whether or not the paste went through (item 555),
on a fake pasteboard: nothing here touches the clipboard of this Mac."""

from collections.abc import Sequence
from types import SimpleNamespace

import pytest
from AppKit import NSPasteboardItem

from dotagent.core.errors import DesktopError
from dotagent.desktop import input as hid

_PLAIN: str = "public.utf8-plain-text"


class _Item:
    def __init__(self, data: dict[str, bytes]) -> None:
        self._data: dict[str, bytes] = data

    def types(self) -> Sequence[str]:
        return tuple(self._data)

    def dataForType_(self, data_type: str) -> bytes | None:
        return self._data.get(data_type)


class _Pasteboard:
    """A clipboard of one item that counts its changes as the real one does; what it refuses is ``refuses``, and what
    someone else puts on it while the paste waits is ``taken_over_by``."""

    def __init__(self, items: list[dict[str, bytes]], refuses: bool = False) -> None:
        self.items: list[dict[str, bytes]] = items
        self.refuses: bool = refuses
        self.count: int = 1
        self.taken_over_by: dict[str, bytes] | None = None

    def pasteboardItems(self) -> Sequence[_Item]:
        return [_Item(data) for data in self.items]

    def clearContents(self) -> int:
        self.items = []
        self.count += 1
        return self.count

    def setString_forType_(self, text: str, data_type: str) -> bool:
        if self.refuses:
            return False
        self.items = [{data_type: text.encode()}]
        self.count += 1
        return True

    def stringForType_(self, data_type: str) -> str | None:
        return None

    def changeCount(self) -> int:
        return self.count

    def writeObjects_(self, objects: Sequence[NSPasteboardItem]) -> bool:
        self.items = [{str(kind): bytes(item.dataForType_(kind)) for kind in item.types()} for item in objects]
        self.count += 1
        return True


def _patched(monkeypatch: pytest.MonkeyPatch, board: _Pasteboard, posted: list[object]) -> None:
    # The name the module reaches the clipboard by is replaced, never the class: nothing here may find the real one.
    monkeypatch.setattr(hid, "NSPasteboard", SimpleNamespace(generalPasteboard=lambda: board))
    monkeypatch.setattr(hid, "chord_events", lambda source, chord: [chord])

    def post(events: Sequence[object]) -> None:
        posted.extend(events)
        if board.taken_over_by is not None:
            board.items = [board.taken_over_by]
            board.count += 1

    monkeypatch.setattr(hid, "post", post)


_COPIED: dict[str, bytes] = {_PLAIN: b"what the owner copied", "public.png": b"\x89PNG-bytes"}


def test_the_clipboard_is_put_back_after_a_paste(monkeypatch: pytest.MonkeyPatch) -> None:
    board: _Pasteboard = _Pasteboard([dict(_COPIED)])
    posted: list[object] = []
    _patched(monkeypatch, board, posted)
    hid.paste_text(object(), "a long text", 0.0)
    assert len(posted) == 1
    assert board.items == [_COPIED]


def test_the_clipboard_is_put_back_when_it_refuses_the_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """The audit's F93: a pasteboard that refused the text failed the step with the owner's clipboard cleared."""
    board: _Pasteboard = _Pasteboard([dict(_COPIED)], refuses=True)
    posted: list[object] = []
    _patched(monkeypatch, board, posted)
    with pytest.raises(DesktopError, match="the clipboard refused the text"):
        hid.paste_text(object(), "a long text", 0.0)
    assert posted == []
    assert board.items == [_COPIED]


def test_the_clipboard_is_put_back_when_the_keys_cannot_be_posted(monkeypatch: pytest.MonkeyPatch) -> None:
    board: _Pasteboard = _Pasteboard([dict(_COPIED)])
    _patched(monkeypatch, board, [])

    def broken(events: Sequence[object]) -> None:
        raise DesktopError("the event tap is gone", {})

    monkeypatch.setattr(hid, "post", broken)
    with pytest.raises(DesktopError, match="the event tap is gone"):
        hid.paste_text(object(), "a long text", 0.0)
    assert board.items == [_COPIED]


def test_a_clipboard_someone_else_filled_while_the_paste_waited_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    board: _Pasteboard = _Pasteboard([dict(_COPIED)])
    board.taken_over_by = {_PLAIN: b"copied meanwhile"}
    _patched(monkeypatch, board, [])
    hid.paste_text(object(), "a long text", 0.0)
    assert board.items == [{_PLAIN: b"copied meanwhile"}]
