"""The help a run can find on this Mac (item 163): the ranking of skills, MCP tools and scriptable applications for a
need, by words and by meaning (item 169), the embeddings that meaning is found by, and what an application's
Info.plist and scripting dictionary say about it, read from bundles made here."""

import asyncio
import plistlib
import shlex
import time
from collections.abc import Sequence
from pathlib import Path

import pytest

from dotagent.help import meaning
from dotagent.help.apps import scriptable_apps
from dotagent.help.meaning import _BATCH, _COLD_TEXTS, _KEPT_TEXTS, HelpVectors, HelpVectorsCold
from dotagent.help.rank import rank, words
from dotagent.help.types import HelpItem
from dotagent.memory.ranking import Vector

_SDEF: str = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE dictionary SYSTEM "file://localhost/System/Library/DTDs/sdef.dtd">
<dictionary title="Brewer Terminology">
  <suite name="Standard Suite" code="core" description="Common classes and commands for all applications.">
    <command name="open" code="aevtodoc" description="Open a document."/>
    <class name="window" code="cwin" description="A window."/>
  </suite>
  <suite name="Brewer Suite" code="brew" description="Brewing coffee.">
    <command name="brew coffee" code="brewcofe" description="Brew a cup of coffee."/>
    <class name="cup" code="bcup" description="A cup of coffee."/>
    <class name="grinder" code="bgrd" description="The coffee grinder."/>
  </suite>
</dictionary>
"""


class _RecordingEmbedder:
    """An embedding service that records the texts of each request: a text about a report points one way, any other
    text the other way."""

    def __init__(self) -> None:
        self.requests: list[list[str]] = []

    @property
    def model(self) -> str:
        return "recording"

    async def embed(self, texts: Sequence[str]) -> list[Vector]:
        self.requests.append(list(texts))
        return [(1.0, 0.0) if "rapor" in text or "report" in text else (0.0, 1.0) for text in texts]

    async def aclose(self) -> None:
        return None


def _item(kind: str, name: str, text: str) -> HelpItem:
    return HelpItem.model_validate({"kind": kind, "name": name, "summary": text, "use": "use it", "text": text})


def _bundle(root: Path, name: str, info: dict[str, object], sdef: str | None) -> None:
    contents: Path = root / f"{name}.app" / "Contents"
    (contents / "Resources").mkdir(parents=True)
    with (contents / "Info.plist").open("wb") as handle:
        plistlib.dump(info, handle)
    if sdef is not None:
        (contents / "Resources" / f"{name}.sdef").write_text(sdef, encoding="utf-8")


def test_words_are_compared_in_lower_case_without_common_words_plurals_or_underscores() -> None:
    assert words("Add the Events to my_calendar, weekly-report") == ["add", "event", "calendar", "weekly", "report"]
    assert words("Classes, repositories and branches; its status") == ["class", "repository", "branch", "status"]


def test_the_item_that_shares_the_most_telling_words_with_the_need_comes_first() -> None:
    items: list[HelpItem] = [
        _item("skill", "weekly-report", "weekly-report Write the weekly report from the notes of the week."),
        _item("mcp_tool", "mcp__github__create_issue", "mcp__github__create_issue Create an issue in a repository."),
        _item("app", "Calendar", "Calendar Calendar calendar event attendee Make a new event in a calendar."),
        _item("app", "Mail", "Mail Mail message mailbox account Send a message."),
        _item("app", "Database Events", "Database Events Database Events"),
    ]
    # Database Events shares only "event" with the need, and scores less than half as well as Calendar.
    assert [item.name for item in rank(items, "add an event to a calendar", [], 3)] == ["Calendar"]
    assert [item.name for item in rank(items, "open an issue on GitHub", [], 3)] == ["mcp__github__create_issue"]
    assert [item.name for item in rank(items, "send a message", [], 3)] == ["Mail"]
    assert rank(items, "bake a cake", [], 3) == []


def test_an_item_close_to_the_need_in_meaning_is_found_though_it_shares_no_word_with_it() -> None:
    items: list[HelpItem] = [
        _item("skill", "weekly-report", "weekly-report Write the weekly report from the notes of the week."),
        _item("app", "Calendar", "Calendar Calendar calendar event attendee Make a new event in a calendar."),
        _item("app", "Mail", "Mail Mail message mailbox account Send a message."),
    ]
    # A need in Turkish shares no word with any item; the embeddings put Calendar (place 1) close to it.
    assert rank(items, "takvime bir etkinlik ekle", [], 3) == []
    assert [item.name for item in rank(items, "takvime bir etkinlik ekle", [1], 3)] == ["Calendar"]
    # An item both rankings hold comes before one that only the meaning holds.
    assert [item.name for item in rank(items, "add an event to a calendar", [2, 1], 3)] == ["Calendar", "Mail"]


async def test_each_text_is_embedded_once_before_the_need_and_the_close_ones_are_found() -> None:
    embedder: _RecordingEmbedder = _RecordingEmbedder()
    vectors: HelpVectors = HelpVectors(embedder, 0.5, None)
    report: str = "weekly-report Write the weekly report."
    invoice: str = "invoice-pdf Fill in an invoice."
    monthly: str = "monthly-report Write the monthly report."
    assert await vectors.closest("haftalık rapor", [report, invoice, report]) == [0, 2]
    assert await vectors.closest("bir rapor yaz", [report, invoice, monthly]) == [0, 2]
    # The texts not embedded before, each once, then the need (item 404: a need alone is cheap to send again).
    assert embedder.requests == [[report, invoice], ["haftalık rapor"], [monthly], ["bir rapor yaz"]]


async def test_texts_are_embedded_in_batches_and_a_cold_search_warms_them_in_the_background() -> None:
    """More than _COLD_TEXTS unembedded texts: the search does not wait (HelpVectorsCold) and the texts are embedded
    behind it, _BATCH at a time (item 404); the next search finds them ready."""
    embedder: _RecordingEmbedder = _RecordingEmbedder()
    vectors: HelpVectors = HelpVectors(embedder, 0.5, None)
    tools: list[str] = [f"tool {number}" for number in range(_COLD_TEXTS + 10)]
    with pytest.raises(HelpVectorsCold) as cold:
        await vectors.closest("need", tools)
    assert cold.value.missing == _COLD_TEXTS + 10
    assert vectors._warming is not None
    await vectors._warming
    assert [len(request) for request in embedder.requests] == [_BATCH, _BATCH, (_COLD_TEXTS + 10) % _BATCH]
    # Warm, the next search embeds the need alone and ranks every text (all point the same way here).
    assert len(await vectors.closest("need", tools)) == len(tools)
    assert embedder.requests[-1] == ["need"]


async def test_past_its_bound_only_the_texts_of_the_latest_search_stay_embedded() -> None:
    embedder: _RecordingEmbedder = _RecordingEmbedder()
    vectors: HelpVectors = HelpVectors(embedder, 0.5, None)
    tools: list[str] = [f"tool {number}" for number in range(_KEPT_TEXTS)]
    assert await vectors.prepare(tools) == _KEPT_TEXTS
    await vectors.closest("need", ["one more"])
    await vectors.closest("need", [tools[0], "one more"])
    assert embedder.requests[-4:] == [["one more"], ["need"], [tools[0]], ["need"]]


class _Keeper:
    """A keeper of help vectors in memory, as the store is on disk."""

    def __init__(self) -> None:
        self.kept: dict[tuple[str, str], tuple[float, ...]] = {}
        self.used: dict[tuple[str, str], float] = {}
        self.asked: list[list[str]] = []

    async def help_vectors(self, model: str) -> dict[str, tuple[float, ...]]:
        return {text: vector for (kept_model, text), vector in self.kept.items() if kept_model == model}

    async def help_vectors_of(self, model: str, texts: Sequence[str]) -> dict[str, tuple[float, ...]]:
        self.asked.append(list(texts))
        return {text: self.kept[(model, text)] for text in texts if (model, text) in self.kept}

    async def add_help_vectors(self, model: str, vectors: Sequence[tuple[str, Sequence[float]]], now: float) -> None:
        for text, vector in vectors:
            self.kept[(model, text)] = tuple(vector)
            self.used[(model, text)] = now

    async def touch_help_vectors(self, model: str, texts: Sequence[str], now: float) -> None:
        for text in texts:
            self.used[(model, text)] = now

    async def prune_help_vectors(self, model: str, before: float) -> int:
        gone: list[tuple[str, str]] = [key for key, used in self.used.items() if key[0] != model or used < before]
        for key in gone:
            del self.kept[key]
            del self.used[key]
        return len(gone)


async def test_the_vectors_kept_outlive_the_process_through_the_keeper() -> None:
    keeper: _Keeper = _Keeper()
    first: _RecordingEmbedder = _RecordingEmbedder()
    report: str = "weekly-report Write the weekly report."
    invoice: str = "invoice-pdf Fill in an invoice."
    await HelpVectors(first, 0.5, keeper).prepare([report, invoice])
    assert set(keeper.kept) == {("recording", report), ("recording", invoice)}
    # A new process: the texts kept are not embedded again, only the need.
    second: _RecordingEmbedder = _RecordingEmbedder()
    vectors: HelpVectors = HelpVectors(second, 0.5, keeper)
    assert await vectors.closest("haftalık rapor", [report, invoice]) == [0]
    assert second.requests == [["haftalık rapor"]]
    # A search tells the keeper its texts are in use, once a process; what no search asked for in a month goes at the
    # next warming, and another model's vectors with it.
    keeper.used[("recording", invoice)] = 1.0
    keeper.used[("recording", report)] = 1.0
    keeper.kept[("older-model", report)] = (1.0, 0.0)
    keeper.used[("older-model", report)] = time.time()
    third: HelpVectors = HelpVectors(_RecordingEmbedder(), 0.5, keeper)
    assert await third.closest("rapor", [report]) == [0]
    assert keeper.used[("recording", report)] > 1.0 and keeper.used[("recording", invoice)] == 1.0
    assert await third.prune() == 2
    assert set(keeper.kept) == {("recording", report)}


class _GatedEmbedder(_RecordingEmbedder):
    """An embedding service that answers once ``gate`` is set: a warming that is still running."""

    def __init__(self) -> None:
        super().__init__()
        self.gate: asyncio.Event = asyncio.Event()

    async def embed(self, texts: Sequence[str]) -> list[Vector]:
        await self.gate.wait()
        return await super().embed(texts)


async def test_texts_asked_for_while_a_warming_runs_are_taken_by_it_next() -> None:
    embedder: _GatedEmbedder = _GatedEmbedder()
    vectors: HelpVectors = HelpVectors(embedder, 0.5, None)
    first: asyncio.Task[None] = vectors.warm(["weekly-report Write the weekly report."])
    await asyncio.sleep(0)
    assert vectors.warm(["invoice-pdf Fill in an invoice."]) is first
    embedder.gate.set()
    await first
    assert [text for request in embedder.requests for text in request] == [
        "weekly-report Write the weekly report.",
        "invoice-pdf Fill in an invoice.",
    ]


async def test_a_text_cut_from_the_process_is_read_back_from_the_keeper_not_embedded_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(meaning, "_KEPT_TEXTS", 1)
    keeper: _Keeper = _Keeper()
    embedder: _RecordingEmbedder = _RecordingEmbedder()
    vectors: HelpVectors = HelpVectors(embedder, 0.5, keeper)
    report: str = "weekly-report Write the weekly report."
    invoice: str = "invoice-pdf Fill in an invoice."
    assert await vectors.closest("rapor", [report, invoice]) == [0]
    # Past the bound, the process keeps the last search's texts alone; the one cut comes back from the keeper.
    assert await vectors.closest("fatura", [invoice]) == [0]
    assert await vectors.closest("rapor", [report]) == [0]
    assert keeper.asked == [[report]]
    assert [request for request in embedder.requests if report in request] == [[report, invoice]]


def test_a_scriptable_application_is_told_by_its_own_dictionary(tmp_path: Path) -> None:
    _bundle(tmp_path, "Brewer", {"OSAScriptingDefinition": "Brewer.sdef"}, _SDEF)
    _bundle(tmp_path, "Plain", {"CFBundleName": "Plain"}, None)
    _bundle(tmp_path, "Older", {"NSAppleScriptEnabled": True}, None)
    _bundle(tmp_path, "Broken", {"OSAScriptingDefinition": "Broken.sdef"}, "<dictionary><suite name=")
    found: dict[str, HelpItem] = {item.name: item for item in scriptable_apps([tmp_path])}
    assert sorted(found) == ["Brewer", "Broken", "Older"]
    brewer: HelpItem = found["Brewer"]
    assert "commands brew coffee" in brewer.summary
    assert "classes cup, grinder" in brewer.summary
    # The standard suite every application shares is ranked, not listed.
    assert "open" not in brewer.summary
    assert {"open", "brew", "coffee", "grinder"} <= set(words(brewer.text))
    assert 'tell application "Brewer"' in brewer.use
    # An older application's dictionary is in no file of its own: sdef prints it.
    assert "no file of its own" in found["Older"].summary
    assert f"sdef {shlex.quote(str(tmp_path / 'Older.app'))} in the shell" in found["Older"].use
    assert "could not be read" in found["Broken"].summary
