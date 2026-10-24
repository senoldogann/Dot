"""The words Dot sends on a channel (item 572): an answer as plain text, cut to the channel's size with a link to the
chat for the rest, and the one line that tells a run waits for the owner. A messaging app shows what it is given as it
is: no Markdown is drawn, so what an answer holds of it is read as plain text (item 617): a list item with a bullet, a
heading on a line of its own, a table a row to a line, and none of the marks of emphasis, quotes and rules."""

import re
from collections.abc import Sequence
from typing import Literal

from dotagent.core.text import plain_line, text_window

type LineKind = Literal["prose", "fence", "code"]

# A fence's own line (``` or ~~~, with its language, at any indent: a model indents it under a list item) opens or
# closes a block of code. The lines of the block stay as they are written, the marks in them too; the fence lines are
# left out. A line of three backticks with more of them after it is a code span on one line, as it is in Markdown.
_FENCE: re.Pattern[str] = re.compile(r"^[ \t]*(?P<mark>`{3,}(?=[^`\n]*$)|~{3,})[^\n]*$")
_HEADING: re.Pattern[str] = re.compile(r"^[ \t]{0,3}#{1,6}(?:[ \t]+(?P<words>.*?))?(?:[ \t]+#+)?[ \t]*$")
# Three or more of one mark on a line of their own, spaces between them allowed (---, * * *, ___).
_RULE: re.Pattern[str] = re.compile(r"^[ \t]{0,3}(?:(?:-[ \t]*){3,}|(?:\*[ \t]*){3,}|(?:_[ \t]*){3,})$")
_QUOTE: re.Pattern[str] = re.compile(r"^(?:[ \t]{0,3}>[ \t]?)+")
_BULLET: re.Pattern[str] = re.compile(r"^(?P<indent>[ \t]*)[-*+](?:[ \t]+(?P<words>.*))?$")
_NUMBERED: re.Pattern[str] = re.compile(r"^(?P<indent>[ \t]*)(?P<number>\d{1,3}[.)])[ \t]+(?P<words>.*)$")
_TASK: re.Pattern[str] = re.compile(r"^\[(?P<done>[ xX])\][ \t]+")
# A table: its rule line is cells of dashes (with the colons that align them) between pipes.
_TABLE_RULE_CELL: re.Pattern[str] = re.compile(r"^:?-+:?$")
_CELL_BREAK: re.Pattern[str] = re.compile(r"(?<!\\)\|")
# An inline code span keeps what is in it as it is; every other mark is read around it.
_CODE_SPAN: re.Pattern[str] = re.compile(r"(?P<ticks>`+)(?!`)(?P<code>.+?)(?<!`)(?P=ticks)(?!`)")
# A link keeps its address in brackets after its words, as the page it points to would be lost else; so does an image
# (a picture of the Mac's own that goes with the answer never reaches here: see pictures.py).
_LINK: re.Pattern[str] = re.compile(
    r"!?\[(?P<words>[^\]\n]*)\]\((?P<address>(?:[^()\s]|\([^()\s]*\))+)(?:[ \t]+\"[^\"\n]*\")?\)"
)
_AUTOLINK: re.Pattern[str] = re.compile(r"<(https?://[^>\s]+)>")
_LINE_BREAK: re.Pattern[str] = re.compile(r"<br\s*/?>", re.IGNORECASE)
# Bold and strike marks that come in pairs on one line, and the emphasis of one star or one underscore around words:
# a lone star or underscore stays (``snake_case``, a * b, src/*.py).
_PAIRED: re.Pattern[str] = re.compile(r"(\*\*|__|~~)(?=\S)(.+?)(?<=\S)\1")
_STARS: re.Pattern[str] = re.compile(r"(?<![\w*/\\])\*(?=[^\s*])([^*\n]+?)(?<=[^\s*])\*(?![\w*])")
_UNDERSCORES: re.Pattern[str] = re.compile(r"(?<![\w_\\])_(?=[^\s_])([^_\n]+?)(?<=[^\s_])_(?![\w_])")
_BLANK_RUNS: re.Pattern[str] = re.compile(r"\n{3,}")
_TRAILING_SPACES: re.Pattern[str] = re.compile(r"[ \t]+$", re.MULTILINE)
# The marks a list item wears in plain text (a bullet, a nested one, a task's box), and how a table's cells are joined.
BULLET: str = "•"
NESTED_BULLET: str = "◦"
TABLE_JOIN: str = " · "
_EMPTY_CELL: str = "–"
_BOXES: dict[str, str] = {" ": "☐", "x": "☑", "X": "☑"}
# Where a code span waits while the marks around it are read: a private-use pair that no text of an answer holds.
_HOLD_OPEN: str = ""
_HOLD_CLOSE: str = ""
_HELD: re.Pattern[str] = re.compile(f"{_HOLD_OPEN}(\\d+){_HOLD_CLOSE}")
# What a cut leaves of the answer is followed by this line: with the link to the chat, or, when Dot has no address a
# phone can open (item 611), where to look.
_REST: str = "\n\n… The rest of the answer is in Dot: "
_REST_ON_THE_MAC: str = "\n\n… The rest of the answer is in Dot on your Mac."
_TAB_SPACES: int = 4
_PASSES: int = 3


def line_kinds(lines: Sequence[str]) -> list[LineKind]:
    """What each of ``lines`` is: ``prose``, a ``fence`` line (the opening of a block of code with its language, or the
    closing) or a line of ``code`` between two. A fence closes with the mark that opened it, at least as long; one never
    closed holds the rest of the text."""
    kinds: list[LineKind] = []
    fence: str | None = None
    for line in lines:
        found: re.Match[str] | None = _FENCE.match(line)
        if fence is None:
            kinds.append("prose" if found is None else "fence")
            fence = None if found is None else found.group("mark")
        elif found is not None and found.group("mark")[0] == fence[0] and _closes(line, fence):
            kinds.append("fence")
            fence = None
        else:
            kinds.append("code")
    return kinds


def _closes(line: str, fence: str) -> bool:
    """Whether ``line`` is a fence line that closes the fence ``fence``: its mark alone, at least as long."""
    mark: str = line.strip()
    return len(mark) >= len(fence) and mark.strip(fence[0]) == ""


def _cells(line: str) -> list[str]:
    """The cells of a table row: the text between its pipes, the outer pipes left out, an escaped pipe kept as one."""
    row: str = line.strip().removeprefix("|")
    if row.endswith("|") and not row.endswith("\\|"):
        row = row[:-1]
    return [cell.strip().replace("\\|", "|") for cell in _CELL_BREAK.split(row)]


def _is_table_rule(line: str, columns: int) -> bool:
    """Whether ``line`` is the rule line of a table with ``columns`` columns."""
    if "-" not in line:
        return False
    cells: list[str] = _cells(line)
    return len(cells) == columns and all(_TABLE_RULE_CELL.match(cell) is not None for cell in cells)


def _table_rows(lines: Sequence[str], start: int) -> tuple[list[str], int] | None:
    """The rows of the table that starts at ``lines[start]`` as plain lines, and the index after its last line; ``None``
    when no table starts there. A table is a line with pipes, a rule line of as many cells, and the lines with pipes
    that follow. A row is its cells joined by :data:`TABLE_JOIN`, an empty cell written as a dash; the rule line is
    gone."""
    header: str = lines[start]
    if "|" not in header or start + 1 >= len(lines):
        return None
    if not _is_table_rule(lines[start + 1], len(_cells(header))):
        return None
    rows: list[str] = [TABLE_JOIN.join(cell or _EMPTY_CELL for cell in _cells(header))]
    index: int = start + 2
    while index < len(lines) and lines[index].strip() != "" and "|" in lines[index]:
        rows.append(TABLE_JOIN.join(cell or _EMPTY_CELL for cell in _cells(lines[index])))
        index += 1
    return rows, index


def _hold_code(line: str, held: list[str]) -> str:
    """``line`` with each code span replaced by a mark that stands for it, the span (without its backticks) kept in
    ``held``."""

    def hold(span: re.Match[str]) -> str:
        held.append(span.group("code"))
        return f"{_HOLD_OPEN}{len(held) - 1}{_HOLD_CLOSE}"

    return _CODE_SPAN.sub(hold, line)


def _link_words(found: re.Match[str]) -> str:
    words: str = found.group("words")
    address: str = found.group("address")
    return address if words in ("", address) else f"{words} ({address})"


def _inline(line: str) -> str:
    """One line of prose without the marks of Markdown: links read ``words (address)``, bold, strike and emphasis marks
    around words are left out, and a code span keeps what is in it as it is."""
    held: list[str] = []
    text: str = _hold_code(line, held)
    text = _LINE_BREAK.sub(" ", text)
    text = _LINK.sub(_link_words, text)
    text = _AUTOLINK.sub(r"\1", text)
    for _ in range(_PASSES):
        marked: str = _UNDERSCORES.sub(r"\1", _STARS.sub(r"\1", _PAIRED.sub(r"\2", text)))
        if marked == text:
            break
        text = marked
    return _HELD.sub(lambda found: held[int(found.group(1))], text)


class _Lists:
    """The indents of the list items open now, so that an item deeper than the one before it is a nested one, whatever
    the width of its indent."""

    def __init__(self) -> None:
        self._indents: list[int] = []

    def level(self, indent: str) -> int:
        width: int = len(indent.replace("\t", " " * _TAB_SPACES))
        while self._indents and width < self._indents[-1]:
            self._indents.pop()
        if not self._indents or width > self._indents[-1]:
            self._indents.append(width)
        return len(self._indents) - 1

    def close(self) -> None:
        self._indents.clear()


def _list_item(line: str, lists: _Lists) -> str | None:
    """``line`` as a list item in plain text: ``• words``, a nested one ``  ◦ words`` (two spaces more for each level
    below), a task with its box (``☐``, ``☑``), a numbered item as it is written with the indent of its level; ``None``
    for a line that is no list item. An item with no words comes back as its mark alone."""
    bullet: re.Match[str] | None = _BULLET.match(line)
    if bullet is not None:
        level: int = lists.level(bullet.group("indent"))
        words: str = bullet.group("words") or ""
        task: re.Match[str] | None = _TASK.match(words)
        if task is not None:
            return f"{'  ' * level}{_BOXES[task.group('done')]} {words[task.end() :]}".rstrip()
        mark: str = BULLET if level == 0 else NESTED_BULLET
        return f"{'  ' * level}{mark} {words}".rstrip()
    numbered: re.Match[str] | None = _NUMBERED.match(line)
    if numbered is not None:
        level = lists.level(numbered.group("indent"))
        return f"{'  ' * level}{numbered.group('number')} {numbered.group('words')}".rstrip()
    return None


def _prose_line(line: str, out: list[str], lists: _Lists) -> None:
    """Add the plain-text form of one line of prose to ``out``: a heading on a line of its own with a blank line before
    it, a rule gone, a quote's marks gone, a list item with its bullet (and none for an item with no words)."""
    if _RULE.match(line):
        lists.close()
        out.append("")
        return
    heading: re.Match[str] | None = _HEADING.match(line)
    if heading is not None:
        lists.close()
        words: str = _inline(heading.group("words") or "").strip()
        if words != "":
            if out and out[-1] != "":
                out.append("")
            out.append(words)
        return
    unquoted: str = _QUOTE.sub("", line)
    item: str | None = _list_item(unquoted, lists)
    if item is not None:
        if item.strip() not in ("", BULLET, NESTED_BULLET):
            out.append(_inline(item))
        return
    if unquoted.strip() == "":
        out.append("")
        return
    if not unquoted.startswith((" ", "\t")):
        lists.close()
    out.append(_inline(unquoted))


def as_plain_text(markdown: str) -> str:
    """``markdown`` as the plain text a messaging app shows well: a list item with a bullet (``• ``, a nested one
    ``  ◦ ``, a numbered one as it is), a heading on a line of its own with a blank line before it, a table one row to a
    line with its cells joined by `` · `` and its rule line gone, a horizontal rule and a quote's marks gone, fences,
    paired bold, strike, emphasis and code marks left out, a link as ``words (address)``, and runs of blank lines shrunk
    to one. The lines of a block of code stay as they are written."""
    lines: list[str] = markdown.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    kinds: list[LineKind] = line_kinds(lines)
    out: list[str] = []
    lists: _Lists = _Lists()
    index: int = 0
    while index < len(lines):
        if kinds[index] != "prose":
            lists.close()
            if kinds[index] == "code":
                out.append(lines[index])
            index += 1
            continue
        table: tuple[list[str], int] | None = _table_rows(lines, index)
        if table is None:
            _prose_line(lines[index], out, lists)
            index += 1
            continue
        rows, index = table
        lists.close()
        out.extend(_inline(row) for row in rows)
    text: str = _TRAILING_SPACES.sub("", "\n".join(out))
    return _BLANK_RUNS.sub("\n\n", text).strip()


def cut_with_link(text: str, limit: int, link: str | None) -> str:
    """``text`` as it is when it fits ``limit`` characters; else its start, cut at a line or word break when one is
    close, then a line that says where the rest is: ``link``, the chat in Dot, or, with no link (``None``: Dot has no
    address a phone can open), that it is in Dot on the Mac. The result is within ``limit``."""
    if len(text) <= limit:
        return text
    tail: str = _REST_ON_THE_MAC if link is None else f"{_REST}{link}"
    head, _ = text_window(text, 0, max(0, limit - len(tail)))
    return f"{head.rstrip()}{tail}"


def one_line(text: str, chars: int) -> str:
    """``text`` as one line of at most ``chars`` characters, its marks left out and its end marked when it was cut."""
    flat: str = plain_line(text, len(text))
    return flat if len(flat) <= chars else f"{flat[: chars - 1].rstrip()}…"
