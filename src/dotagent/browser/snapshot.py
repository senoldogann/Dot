"""Pure helpers for Playwright AI snapshots: parsing, viewport pruning, compaction, diffing, search and fencing.

A snapshot line looks like ``- button "Save" [ref=e12] [cursor=pointer] [box=10,20,80,32]``. The box is in CSS pixels
relative to the viewport of the frame that owns the element, which is why content of an iframe is compared with the
iframe element's size and not with the page viewport.

The rendered view keeps what the model can act on or read and leaves out scaffolding: anonymous layout wrappers
(``generic``, ``table``, ``cell`` …), separator text such as ``|`` and link targets, which ``link_lines`` serves on demand.

A collapsed native dropdown lists its options under the ``combobox`` line as lines without a ref and without a size,
which the viewport filter would drop. ``fold_options`` moves them onto the dropdown's own line, as
``options "Red" [selected], "Blue"``, so the model sees what it can choose and what is chosen.

What stands in front of the page (a dialog, or a cookie, privacy or newsletter box) is found by ``overlay_lines`` so the
view can show it first: it takes the clicks until it is answered or closed.
"""

import json
import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, replace
from urllib.parse import urljoin

from dotagent.browser.types import Dropdown, DropdownOption
from dotagent.core.text import unclosing

_INDENT: re.Pattern[str] = re.compile(r"^( *)- ")
_BOX: re.Pattern[str] = re.compile(r" \[box=(-?[\d.]+),(-?[\d.]+),([\d.]+),([\d.]+)\]")
_REF: re.Pattern[str] = re.compile(r"\[ref=([^\]]+)\]")
_COSMETIC: re.Pattern[str] = re.compile(r" \[cursor=pointer\]")
_ATTRIBUTE: re.Pattern[str] = re.compile(r"\[([^\]]*)\]")
_QUOTED_KEY: re.Pattern[str] = re.compile(r"^(?P<lead>\s*- )'(?P<key>(?:[^']|'')*)'(?P<tail>:.*)?$")
_SHAPE: re.Pattern[str] = re.compile(
    r'^\s*- (?P<role>/?[\w-]+)(?: "(?P<name>(?:[^"\\]|\\.)*)")?(?P<attrs>(?: \[[^\]]*\])*)(?P<tail>:(?: (?P<inline>.*))?)?$'
)
_NAMED: re.Pattern[str] = re.compile(r'^\s*- (?!generic\b)([A-Za-z][\w-]*) "((?:[^"\\]|\\.)*)"')
_BLANK_RUNS: re.Pattern[str] = re.compile(r"\n{3,}")
_TRAILING_SPACE: re.Pattern[str] = re.compile(r"[ \t]+\n")
_INVISIBLE_CHARS: re.Pattern[str] = re.compile("[" + chr(0x200B) + chr(0x00AD) + "]")  # zero-width space, soft hyphen
_WORD: re.Pattern[str] = re.compile(r"\w{3,}")
_MAX_LINE_CHARS: int = 400
_MAX_LINK_CHARS: int = 300
_CONTEXT_HOPS: int = 6
_OPTIONS_VIEW_CHARS: int = 240  # leaves room on a dropdown's line, which is cut at _MAX_LINE_CHARS, for its name
_OPTION_JOIN: str = ", "

# Roles that only lay out other elements; without a name or text of their own they are not shown.
_TRANSPARENT_ROLES: frozenset[str] = frozenset({"generic", "table", "rowgroup", "cell", "group", "list"})
# Roles that group related elements (a table row, a list item); shown only when something inside them is shown.
_GROUPING_ROLES: frozenset[str] = frozenset({"row", "listitem"})
_SEPARATOR_CHARS: frozenset[str] = frozenset("|()[]{}<>/\\·•–—-,.:;*~»«›‹…&\"'")
_URL_ROLE: str = "/url"
_HIDDEN_SCHEMES: tuple[str, ...] = ("javascript:", "data:")
_DIALOG_ROLES: frozenset[str] = frozenset({"dialog", "alertdialog"})
# Landmarks that are not dialogs by role but still sit in front of the page when their name says what they ask for.
# Only consent banners: words like "subscribe" or "privacy" also name sidebars, footers and inline forms of the page.
_OVERLAY_LANDMARKS: frozenset[str] = frozenset({"region", "banner", "complementary", "contentinfo", "group", "form"})
_OVERLAY_NAME: re.Pattern[str] = re.compile(r"cookie|consent|gdpr|çerez", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class Box:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True, slots=True)
class SnapshotLine:
    depth: int
    text: str  # the original line without its [box=...] and [cursor=...] suffixes
    ref: str | None
    box: Box | None
    role: str  # empty when the line has no recognisable shape
    name: str  # accessible name as written in the line, empty when there is none
    inline: str | None  # the text after the colon when a single text child is shown inline
    nests: bool  # children follow on deeper lines
    attrs: tuple[str, ...]  # the bracketed properties other than the box, such as ref=e12, selected or disabled


@dataclass(frozen=True, slots=True)
class ViewDiff:
    added: tuple[str, ...]
    removed_refs: tuple[str, ...]
    unchanged: int


def _plain_key(text: str) -> str:
    """The line with its YAML-quoted key written out: Playwright quotes a whole key when the name contains ``: ``."""
    quoted: re.Match[str] | None = _QUOTED_KEY.match(text)
    if quoted is None:
        return text
    return f"{quoted.group('lead')}{quoted.group('key').replace(chr(39) * 2, chr(39))}{quoted.group('tail') or ''}"


def _ref_of(text: str, shape: re.Match[str] | None) -> str | None:
    """The ref of a line: one of the properties after its name, as a page can write ``[ref=…]`` into a name to pass one
    control for another (the code review's Y9); in a line of no known shape, its last one."""
    if shape is not None:
        refs: list[str] = _REF.findall(shape.group("attrs"))
    else:
        refs = _REF.findall(text)
    return refs[-1] if refs else None


def _make_line(raw: str, depth: int) -> SnapshotLine:
    box_match: re.Match[str] | None = _BOX.search(raw)
    box: Box | None = None
    if box_match is not None:
        x, y, width, height = (float(group) for group in box_match.groups())
        box = Box(x=x, y=y, width=width, height=height)
    text: str = _COSMETIC.sub("", _BOX.sub("", raw))
    shape: re.Match[str] | None = _SHAPE.match(_plain_key(text))
    return SnapshotLine(
        depth=depth,
        text=text,
        ref=_ref_of(text, shape),
        box=box,
        role=shape.group("role") if shape is not None else "",
        name=(shape.group("name") or "") if shape is not None else "",
        inline=shape.group("inline") if shape is not None else None,
        nests=shape is not None and shape.group("tail") == ":",
        attrs=tuple(_ATTRIBUTE.findall(shape.group("attrs"))) if shape is not None else (),
    )


def parse_snapshot(snapshot: str) -> tuple[SnapshotLine, ...]:
    """Split a snapshot into lines with their depth, ref, box and shape; lines that are not list items are dropped."""
    lines: list[SnapshotLine] = []
    for raw in snapshot.splitlines():
        indent: re.Match[str] | None = _INDENT.match(raw)
        if indent is not None:
            lines.append(_make_line(raw, len(indent.group(1)) // 2))
    return tuple(lines)


def _parents(lines: Sequence[SnapshotLine]) -> list[int | None]:
    """Index of each line's parent (the nearest earlier line with a smaller depth), in one pass."""
    stack: list[int] = []
    parents: list[int | None] = []
    for index, line in enumerate(lines):
        while stack and lines[stack[-1]].depth >= line.depth:
            stack.pop()
        parents.append(stack[-1] if stack else None)
        stack.append(index)
    return parents


def _frame_owners(lines: Sequence[SnapshotLine], parents: Sequence[int | None]) -> list[int | None]:
    """Index of the nearest ancestor that is an iframe element, whose viewport the line's box is relative to."""
    owners: list[int | None] = []
    for index in range(len(lines)):
        parent: int | None = parents[index]
        if parent is None:
            owners.append(None)
        elif lines[parent].role == "iframe":
            owners.append(parent)
        else:
            owners.append(owners[parent])
    return owners


def _intersects(box: Box, width: float, height: float, margin: float) -> bool:
    return box.x < width and box.x + box.width > 0 and box.y < height + margin and box.y + box.height > -margin


def viewport_filter(
    lines: Sequence[SnapshotLine], viewport_width: float, viewport_height: float, margin: float
) -> tuple[SnapshotLine, ...]:
    """Keep the lines whose box meets the viewport (plus ``margin``), their ancestors and their text/property lines."""
    parents: list[int | None] = _parents(lines)
    owners: list[int | None] = _frame_owners(lines, parents)
    visible: list[bool] = []
    for index, line in enumerate(lines):
        owner: int | None = owners[index]
        width: float = viewport_width
        height: float = viewport_height
        if owner is not None:
            owner_box: Box | None = lines[owner].box
            if owner_box is None or not visible[owner]:
                visible.append(False)
                continue
            width, height = owner_box.width, owner_box.height
        visible.append(line.box is not None and _intersects(line.box, width, height, margin))
    keep: list[bool] = list(visible)
    for index in range(len(lines) - 1, -1, -1):  # children come after their parents, so one reverse pass suffices
        parent: int | None = parents[index]
        if keep[index] and parent is not None:
            keep[parent] = True
    for index, line in enumerate(lines):
        owner_parent: int | None = parents[index]
        if line.box is None and owner_parent is not None and visible[owner_parent]:
            keep[index] = True
    return tuple(line for line, kept in zip(lines, keep, strict=True) if kept)


def _opens_overlay(line: SnapshotLine, viewport_width: float, viewport_height: float) -> bool:
    if line.box is None or not _intersects(line.box, viewport_width, viewport_height, 0.0):
        return False
    if line.role in _DIALOG_ROLES:
        return True
    return line.role in _OVERLAY_LANDMARKS and _OVERLAY_NAME.search(line.name) is not None


def overlay_lines(
    lines: Sequence[SnapshotLine], viewport_width: float, viewport_height: float
) -> tuple[tuple[SnapshotLine, ...], int]:
    """The dialogs and the cookie consent banners that are open in the viewport, each with everything inside it,
    in page order, and how many there are. A dialog inside another one counts once, as part of the outer one."""
    kept: list[SnapshotLine] = []
    count: int = 0
    inside_depth: int | None = None
    for line in lines:
        if inside_depth is not None and line.depth > inside_depth:
            kept.append(line)
            continue
        inside_depth = None
        if _opens_overlay(line, viewport_width, viewport_height):
            inside_depth = line.depth
            count += 1
            kept.append(line)
    return tuple(kept), count


@dataclass(frozen=True, slots=True)
class _Layout:
    shown: tuple[bool, ...]  # whether the line appears in the rendered view
    indent: tuple[int, ...]  # nesting level among the shown lines
    has_children: tuple[bool, ...]  # whether a shown line has shown children


def _is_separator_text(line: SnapshotLine) -> bool:
    content: str = (line.inline or "").strip()
    return all(char in _SEPARATOR_CHARS or char.isspace() for char in content)


def _hidden_by_itself(line: SnapshotLine) -> bool:
    """Lines that never reach the model: link targets, separator text and anonymous layout wrappers."""
    if line.role == _URL_ROLE:
        return True
    if line.role == "text":
        return _is_separator_text(line)
    if line.role in _TRANSPARENT_ROLES and line.inline is None:
        return not line.name or (line.role == "generic" and line.nests)
    return False


def _layout(lines: Sequence[SnapshotLine]) -> _Layout:
    parents: list[int | None] = _parents(lines)
    shown: list[bool] = [not _hidden_by_itself(line) for line in lines]
    has_content: list[bool] = [False] * len(lines)
    for index in range(len(lines) - 1, -1, -1):  # children before parents
        line: SnapshotLine = lines[index]
        if shown[index] and line.role in _GROUPING_ROLES and not line.name and line.inline is None:
            shown[index] = has_content[index]
        parent: int | None = parents[index]
        if parent is not None and (shown[index] or has_content[index]):
            has_content[parent] = True
    nearest_shown: list[int | None] = []
    indent: list[int] = []
    for index in range(len(lines)):
        parent = parents[index]
        ancestor: int | None = None if parent is None else (parent if shown[parent] else nearest_shown[parent])
        nearest_shown.append(ancestor)
        indent.append(0 if ancestor is None else indent[ancestor] + 1)
    has_children: list[bool] = [False] * len(lines)
    for index in range(len(lines)):
        ancestor = nearest_shown[index]
        if shown[index] and ancestor is not None:
            has_children[ancestor] = True
    return _Layout(shown=tuple(shown), indent=tuple(indent), has_children=tuple(has_children))


def _clip(text: str) -> str:
    return text if len(text) <= _MAX_LINE_CHARS else f"{text[: _MAX_LINE_CHARS - 1]}…"


def _without_open_colon(line: SnapshotLine, opens_block: bool) -> str:
    text: str = line.text.strip()
    return text[:-1] if line.nests and not opens_block and text.endswith(":") else text


def render_lines(lines: Sequence[SnapshotLine]) -> str:
    """Render lines for the model: scaffolding disappears and the remaining elements move up to fill the gap."""
    layout: _Layout = _layout(lines)
    rendered: list[str] = []
    for index, line in enumerate(lines):
        if layout.shown[index]:
            text: str = _without_open_colon(line, layout.has_children[index])
            rendered.append("  " * layout.indent[index] + _clip(text))
    return "\n".join(rendered)


def count_refs(lines: Sequence[SnapshotLine]) -> int:
    layout: _Layout = _layout(lines)
    return sum(1 for index, line in enumerate(lines) if line.ref is not None and layout.shown[index])


def diff_views(previous: str, current: str) -> ViewDiff:
    """Line-level multiset diff of two rendered views. Refs stay stable while an element's role and name do not."""
    before: Counter[str] = Counter(line.strip() for line in previous.splitlines())
    budget: Counter[str] = Counter(before)
    added: list[str] = []
    current_lines: list[str] = current.splitlines()
    for line in current_lines:
        key: str = line.strip()
        if budget[key] > 0:
            budget[key] -= 1
        else:
            added.append(line)
    after: Counter[str] = Counter(line.strip() for line in current_lines)
    present: set[str] = {ref for line in after for ref in _refs_in(line)}
    gone: set[str] = {ref for line in (before - after) for ref in _refs_in(line)} - present
    return ViewDiff(added=tuple(added), removed_refs=tuple(sorted(gone)), unchanged=len(current_lines) - len(added))


def _refs_in(line: str) -> list[str]:
    ref: str | None = _ref_of(line, _SHAPE.match(_plain_key(line.strip())))
    return [] if ref is None else [ref]


def render_diff(diff: ViewDiff) -> str:
    """The delta as the model reads it: a header with the counts (one plain line when nothing changed, so an act on a
    page that stayed the same costs the model one line, not the page again) and the new or changed lines."""
    header: str = (
        f"# no change since the last observation ({diff.unchanged} lines unchanged); the refs still hold"
        if not diff.added and not diff.removed_refs
        else f"# changed since the last observation: {len(diff.added)} new/changed lines, {diff.unchanged} unchanged"
    )
    if diff.removed_refs:
        header += f"; removed refs: {', '.join(diff.removed_refs[:20])}"
    return "\n".join((header, *diff.added))


def prefer_diff(diff: ViewDiff, current: str) -> bool:
    """A diff is only worth showing when it is clearly smaller than the whole view."""
    added_chars: int = sum(len(line) for line in diff.added)
    return diff.unchanged > 0 and added_chars * 2 < len(current) and len(diff.added) <= 60


def fit_to_budget(text: str, max_chars: int) -> tuple[str, int]:
    """Cut ``text`` at a line boundary so it fits ``max_chars``; returns the text and how many lines were dropped."""
    if len(text) <= max_chars:
        return text, 0
    lines: list[str] = text.splitlines()
    used: int = 0
    kept: list[str] = []
    for line in lines:
        if used + len(line) + 1 > max_chars:
            break
        kept.append(line)
        used += len(line) + 1
    return "\n".join(kept), len(lines) - len(kept)


def _context_label(lines: Sequence[SnapshotLine], parents: Sequence[int | None], index: int) -> str | None:
    """The role and name of the nearest named ancestor, so identical buttons can be told apart."""
    ancestor: int | None = parents[index]
    for _ in range(_CONTEXT_HOPS):
        if ancestor is None:
            return None
        named: re.Match[str] | None = _NAMED.match(lines[ancestor].text)
        if named is not None:
            return f'{named.group(1)} "{named.group(2)[:60]}"'
        ancestor = parents[ancestor]
    return None


def find_lines(lines: Sequence[SnapshotLine], needle: str, limit: int) -> tuple[str, ...]:
    """Shown lines with a ref that contain ``needle`` (any case), each followed by the context it sits in."""
    lowered: str = needle.lower()
    parents: list[int | None] = _parents(lines)
    layout: _Layout = _layout(lines)
    hits: list[str] = []
    for index, line in enumerate(lines):
        if line.ref is None or not layout.shown[index] or lowered not in line.text.lower():
            continue
        label: str | None = _context_label(lines, parents, index)
        suffix: str = f"   (in {label})" if label is not None else ""
        hits.append(_clip(_without_open_colon(line, False)) + suffix)
        if len(hits) >= limit:
            break
    return tuple(hits)


def _link_target(lines: Sequence[SnapshotLine], index: int) -> str | None:
    """The ``/url`` property of the link at ``index``: one of its direct children."""
    depth: int = lines[index].depth
    for child in lines[index + 1 :]:
        if child.depth <= depth:
            return None
        if child.depth == depth + 1 and child.role == _URL_ROLE:
            return child.inline
    return None


def link_lines(lines: Sequence[SnapshotLine], base_url: str, needle: str, limit: int) -> tuple[str, ...]:
    """Links of the whole page as ``ref  text -> absolute URL``; ``needle`` filters on text and URL (any case)."""
    lowered: str = needle.lower()
    hits: list[str] = []
    for index, line in enumerate(lines):
        if line.role != "link" or line.ref is None:
            continue
        href: str | None = _link_target(lines, index)
        if href is None or href.lower().startswith(_HIDDEN_SCHEMES):
            continue
        target: str = urljoin(base_url, href)
        shown_target: str = target if len(target) <= _MAX_LINK_CHARS else f"{target[: _MAX_LINK_CHARS - 1]}…"
        label: str = " ".join(line.name.split()) or "(no text)"
        if lowered in f"{label} {target}".lower():
            hits.append(f"{line.ref}  {label} -> {shown_target}")
            if len(hits) >= limit:
                break
    return tuple(hits)


def line_for_ref(lines: Sequence[SnapshotLine], ref: str) -> str | None:
    for line in lines:
        if line.ref == ref:
            return line.text.strip()
    return None


@dataclass(frozen=True, slots=True)
class OptionEntry:
    """One option as the view shows it: the name already quoted, as the snapshot quotes the names of elements."""

    name: str
    selected: bool
    disabled: bool


def _label_of(text: str) -> str:
    """A label as Playwright compares labels: without zero-width and soft-hyphen characters, whitespace collapsed."""
    return " ".join(_INVISIBLE_CHARS.sub("", text).split())


def has_option(dropdown: Dropdown, wanted: str) -> bool:
    """Whether choosing ``wanted`` finds an option: Playwright takes a value as it is and a label ignoring whitespace."""
    target: str = _label_of(wanted)
    return any(wanted == option.value or target == _label_of(option.label) for option in dropdown.options)


def entries_of(options: Sequence[DropdownOption]) -> tuple[OptionEntry, ...]:
    """The options read off a page as entries; a label that is empty is shown by its value."""
    return tuple(
        OptionEntry(
            name=json.dumps(_label_of(option.label) or option.value, ensure_ascii=False),
            selected=option.selected,
            disabled=option.disabled,
        )
        for option in options
    )


def _entry_text(entry: OptionEntry) -> str:
    return entry.name + (" [selected]" if entry.selected else "") + (" [disabled]" if entry.disabled else "")


def render_options(entries: Sequence[OptionEntry], budget: int) -> str:
    """The options in the order given, as many as fit ``budget``; the rest is counted. A choice that does not fit is
    named after the count, so the current value is never hidden."""
    if not entries:
        return "no options"
    shown: list[str] = []
    used: int = 0
    for entry in entries:
        text: str = _entry_text(entry)
        if shown and used + len(text) > budget:
            break
        shown.append(text)
        used += len(text) + len(_OPTION_JOIN)
    listed: str = "options " + _OPTION_JOIN.join(shown)
    hidden: Sequence[OptionEntry] = entries[len(shown) :]
    if not hidden:
        return listed
    chosen: str = _OPTION_JOIN.join(entry.name for entry in hidden if entry.selected)
    return f"{listed}{_OPTION_JOIN}… and {len(hidden)} more" + (f" (selected: {chosen})" if chosen else "")


def closest_first(options: Sequence[DropdownOption], wanted: str) -> tuple[DropdownOption, ...]:
    """The options with those that share the most words with ``wanted`` in front; ties keep the page order."""
    words: list[str] = _WORD.findall(_label_of(wanted).casefold())

    def shared(option: DropdownOption) -> int:
        text: str = f"{_label_of(option.label)} {option.value}".casefold()
        return sum(1 for word in words if word in text)

    return tuple(sorted(options, key=lambda option: -shared(option)))


def _is_hidden_option(line: SnapshotLine) -> bool:
    return line.role == "option" and line.ref is None and not line.nests


def _option_entry(line: SnapshotLine) -> OptionEntry:
    return OptionEntry(name=f'"{line.name}"', selected="selected" in line.attrs, disabled="disabled" in line.attrs)


def fold_options(lines: Sequence[SnapshotLine]) -> tuple[SnapshotLine, ...]:
    """Write the options of each collapsed native dropdown on its own line and drop the option lines.

    A ``<select>`` that is not open lists its options under the ``combobox`` line without a ref and without a size, so
    the viewport filter would lose them. Elements that list options with refs (list boxes, custom widgets) are left as
    they are, because those options can be acted on.
    """
    parents: list[int | None] = _parents(lines)
    options_of: dict[int, list[int]] = {}
    for index, parent in enumerate(parents):
        if parent is not None:
            options_of.setdefault(parent, []).append(index)
    folded: dict[int, SnapshotLine] = {}
    dropped: set[int] = set()
    for index, line in enumerate(lines):
        children: list[int] = options_of.get(index, [])
        if line.role != "combobox" or not line.nests or not children:
            continue
        if not all(_is_hidden_option(lines[child]) for child in children):
            continue
        listed: str = render_options([_option_entry(lines[child]) for child in children], _OPTIONS_VIEW_CHARS)
        folded[index] = replace(line, text=f"{line.text.rstrip()} {listed}", inline=listed, nests=False)
        dropped.update(children)
    return tuple(folded.get(index, line) for index, line in enumerate(lines) if index not in dropped)


def fence_untrusted(url: str, body: str, nonce: str) -> str:
    """Wrap page-derived text so the prompt can state that everything inside is data and only ``nonce`` closes it."""
    safe: str = unclosing(body, "web_content")
    quoted: str = '"' + url.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return f'<web_content nonce="{nonce}" url={quoted}>\n{safe}\n</web_content nonce="{nonce}">'


def squeeze_text(text: str) -> str:
    """Page text without trailing blanks and with at most one empty line in a row."""
    return _BLANK_RUNS.sub("\n\n", _TRAILING_SPACE.sub("\n", text.replace("\r\n", "\n"))).strip()
