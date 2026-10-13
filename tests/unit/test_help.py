"""The help a run can find on this Mac (item 163): the ranking of skills, MCP tools and scriptable applications for a
need, and what an application's Info.plist and scripting dictionary say about it, read from bundles made here."""

import plistlib
import shlex
from pathlib import Path

from dotagent.help.apps import scriptable_apps
from dotagent.help.rank import rank, words
from dotagent.help.types import HelpItem

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
    assert [item.name for item in rank(items, "add an event to a calendar", 3)] == ["Calendar"]
    assert [item.name for item in rank(items, "open an issue on GitHub", 3)] == ["mcp__github__create_issue"]
    assert [item.name for item in rank(items, "send a message", 3)] == ["Mail"]
    assert rank(items, "bake a cake", 3) == []


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
