"""File tools: read, write, edit, list and search, with path-aware risk classification."""

import asyncio
import contextlib
import difflib
import os
import re
import shutil
import tempfile
from collections.abc import Iterator
from itertools import islice
from pathlib import Path

from Foundation import NSURL
from pydantic import Field
from Quartz import PDFDocument

from dotagent.core.errors import ToolExecutionError
from dotagent.core.images import EncodedImage, encode_image
from dotagent.core.text import truncate_middle
from dotagent.office import OfficeKind, names_office_file, office_kind, office_text, unreadable_office_file
from dotagent.policy.paths import CHANGES_DOT, PathClass, classify_path, expand_path
from dotagent.policy.types import ActionInfo, Flag, Risk
from dotagent.tools.base import (
    ACTING_MODES,
    ALL_MODES,
    RegisteredTool,
    ToolArgs,
    ToolResult,
    make_path_tool,
    mark_untrusted,
    ok,
    ok_with_images,
)
from dotagent.tools.context import ToolContext, run_sandbox
from dotagent.tools.third_party import holds_third_party, matches_third_party
from dotagent.undo.journal import JournalTarget, note_change, run_journal

_IMAGE_SUFFIXES: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".heic"})
_DEFAULT_LINE_LIMIT: int = 2000
_MAX_LINE_CHARS: int = 2000
_HEADROOM_CHARS: int = 200  # room next to a page for the line that says where it continues
_LIST_LIMIT: int = 300
_SKIPPED_DIRS: frozenset[str] = frozenset(
    {".git", "node_modules", "__pycache__", ".venv", ".mypy_cache", ".ruff_cache"}
)
_SEARCH_LINE_LIMIT: int = 300
# ripgrep ends with 2 when it met an error, also after the matches it found.
_RG_ERROR: int = 2
# Its line for a path it did not read: "rg: <path>: [IO error for operation on <path>: ]<reason> (os error <n>)", with
# EPERM (1, the sandbox), EACCES (13, the permissions of the file) or EINTR (4: macOS breaks off now and then the
# reading of a folder of a large tree, such as those under ~/Library/Containers, which reads a moment later).
_UNREAD_PATH: re.Pattern[str] = re.compile(r"rg: (.+ \(os error (?:1|4|13)\))")
_UNREAD_SHOWN: int = 10


def resolve_path(raw: str, ctx: ToolContext) -> Path:
    return expand_path(raw, ctx.workspace)


def read_flags(path: Path, ctx: ToolContext) -> tuple[Flag, ...]:
    return _read_flags(path, ctx.workspace, ctx.runtime.protected, ctx.runtime.dot_paths)


def _read_flags(
    path: Path, workspace: Path, protected: tuple[Path, ...], dot_paths: tuple[Path, ...]
) -> tuple[Flag, ...]:
    """The flags of reading the resolved ``path``: a protected location holds secrets."""
    path_class: PathClass = classify_path(path, workspace, protected, dot_paths)
    return ("secret_access",) if path_class == "protected" else ()


def _read_action(tool: str, path: Path, ctx: ToolContext, verb: str) -> ActionInfo:
    return read_action(tool, path, ctx.workspace, ctx.runtime.protected, ctx.runtime.dot_paths, verb)


def read_action(
    tool: str, path: Path, workspace: Path, protected: tuple[Path, ...], dot_paths: tuple[Path, ...], verb: str
) -> ActionInfo:
    """Reading the resolved ``path``: safe, but for a protected location, which holds secrets."""
    return ActionInfo(
        tool=tool,
        kind="read",
        risk="safe",
        summary=f"{verb} {path}",
        target=str(path),
        flags=_read_flags(path, workspace, protected, dot_paths),
    )


def _write_action(tool: str, path: Path, ctx: ToolContext, verb: str) -> ActionInfo:
    return write_action(tool, path, ctx.workspace, ctx.runtime.protected, ctx.runtime.dot_paths, verb)


def write_action(
    tool: str, path: Path, workspace: Path, protected: tuple[Path, ...], dot_paths: tuple[Path, ...], verb: str
) -> ActionInfo:
    """Writing the resolved ``path``, as the policy weighs it by where the path is."""
    path_class: PathClass = classify_path(path, workspace, protected, dot_paths)
    risk: Risk
    flags: tuple[Flag, ...]
    summary: str = f"{verb} {path}"
    if path_class in ("workspace", "temp"):
        risk, flags = "low", ()
    elif path_class == "home":
        risk, flags = "medium", ("outside_workspace",)
    elif path_class == "system":
        risk, flags = "high", ("system_settings", "outside_workspace")
    elif path_class == "dot":
        risk, flags, summary = "high", ("self_change",), f"{summary} ({CHANGES_DOT})"
    else:
        risk, flags = "high", ("secret_access",)
    return ActionInfo(tool=tool, kind="write", risk=risk, summary=summary, target=str(path), flags=flags)


def _pdf_text(path: Path) -> str:
    document = PDFDocument.alloc().initWithURL_(NSURL.fileURLWithPath_(str(path)))
    if document is None:
        raise ToolExecutionError("the PDF could not be opened", {"path": str(path)})
    return str(document.string() or "")


def _is_binary(path: Path) -> bool:
    with path.open("rb") as handle:
        return b"\0" in handle.read(8192)


def _numbered(lines: Iterator[str], path: Path, offset: int, limit: int, budget: int) -> str:
    """The lines of ``path`` from ``offset`` (1-based), numbered, cut by line count and by character budget, with the
    offset to go on from."""
    window: list[str] = list(islice(lines, offset - 1, offset - 1 + limit))
    more: bool = next(lines, None) is not None
    shown: list[str] = []
    used: int = 0
    truncated: bool = False
    for index, line in enumerate(window):
        rendered: str = f"{offset + index:>6}\t{truncate_middle(line.rstrip(chr(10)), _MAX_LINE_CHARS)}"
        if used + len(rendered) > budget and shown:
            truncated = True
            break
        shown.append(rendered)
        used += len(rendered) + 1
    if not shown:
        return f"(empty file or offset {offset} is past the end) {path}"
    last: int = offset + len(shown) - 1
    if truncated or more:
        return "\n".join(shown) + f"\n[showing lines {offset}-{last}; pass offset={last + 1} to continue]"
    return "\n".join(shown) + f"\n[lines {offset}-{last}; end of file]"


def _numbered_window(path: Path, offset: int, limit: int, budget: int) -> str:
    """Numbered lines of a text file starting at ``offset`` (1-based), cut by line count and by character budget."""
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return _numbered(handle, path, offset, limit, budget)


def _read_blocking(path: Path, offset: int, limit: int, budget: int, max_edge: int, quality: int) -> ToolResult:
    if not path.exists():
        raise ToolExecutionError("file does not exist", {"path": str(path)})
    if path.is_dir():
        raise ToolExecutionError("path is a directory; use list_dir", {"path": str(path)})
    suffix: str = path.suffix.lower()
    if suffix in _IMAGE_SUFFIXES:
        encoded: EncodedImage = encode_image(path.read_bytes(), max_edge, quality)
        return ok_with_images(f"Image {path.name}: {encoded.source_width}x{encoded.source_height}px", (encoded.image,))
    if suffix == ".pdf":
        # The text of a PDF is paged by its lines like a text file's, so no part of a long one is out of reach.
        text: str = _pdf_text(path)
        if not text:
            return ok("(the PDF contains no extractable text)")
        return ok(_numbered(iter(text.splitlines()), path, offset, limit, budget))
    if names_office_file(path.name):
        # A Word, Excel or PowerPoint file reads as its text (dotagent.office), paged by its lines like a PDF's.
        data: bytes = path.read_bytes()
        unreadable: str | None = unreadable_office_file(path.name, data)
        if unreadable is not None:
            raise ToolExecutionError(unreadable, {"path": str(path)})
        office: OfficeKind | None = office_kind(path.name, data)
        if office is not None:
            written: str = office_text(office, data)
            if not written:
                return ok("(the file holds no text)")
            return ok(_numbered(iter(written.splitlines()), path, offset, limit, budget))
    if _is_binary(path):
        return ok(f"Binary file ({path.stat().st_size} bytes): {path}")
    return ok(_numbered_window(path, offset, limit, budget))


class ReadFileArgs(ToolArgs):
    path: str = Field(description="File to read. Relative paths resolve against the workspace.")
    offset: int | None = Field(default=None, ge=1, description="First line to return (1-based).")
    limit: int | None = Field(default=None, ge=1, description="Maximum number of lines to return.")


def _read_file_tool() -> RegisteredTool:
    def classify(args: ReadFileArgs, ctx: ToolContext) -> ActionInfo:
        return _read_action("read_file", resolve_path(args.path, ctx), ctx, "Read")

    async def run(args: ReadFileArgs, ctx: ToolContext, authorized: ActionInfo) -> ToolResult:
        desktop = ctx.runtime.config.desktop
        path: Path = resolve_path(args.path, ctx)
        read: ToolResult = await asyncio.to_thread(
            _read_blocking,
            path,
            args.offset or 1,
            args.limit or _DEFAULT_LINE_LIMIT,
            ctx.output_chars - _HEADROOM_CHARS,
            desktop.screenshot_max_edge,
            desktop.jpeg_quality,
        )
        # A page a crawl kept or an MCP answer kept whole was written by third parties (item 494).
        return mark_untrusted(read) if holds_third_party(path, ctx.workspace) else read

    return make_path_tool(
        name="read_file",
        description=(
            "Read a text file with line numbers (also images, PDFs and Word, Excel and PowerPoint files). Use "
            "offset/limit for large files."
        ),
        args_model=ReadFileArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=60.0,
        classify=classify,
        run=run,
    )


def journal_for(ctx: ToolContext, tool: str) -> JournalTarget:
    """Where ``tool`` notes a change of this run for ``dotx undo``, and how much it may copy."""
    return JournalTarget(
        directory=run_journal(ctx.runtime.undo_dir, ctx.run_id),
        tool=tool,
        max_file_bytes=ctx.runtime.config.agent.undo_max_file_bytes,
        max_run_bytes=ctx.runtime.config.agent.undo_max_run_bytes,
    )


def atomic_write(path: Path, content: str, journal: JournalTarget) -> int:
    """Replace ``path`` with ``content`` through a temporary file, keeping its permission bits. The change is noted in
    ``journal`` first; when that fails, the file is not changed."""
    if path.is_dir():
        raise ToolExecutionError("path is a directory", {"path": str(path)})
    data: bytes = content.encode("utf-8")
    note_change(journal, path, data)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode: int | None = path.stat().st_mode & 0o777 if path.exists() else None
    handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
        if mode is not None:
            os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    except OSError:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return len(data)


class WriteFileArgs(ToolArgs):
    path: str = Field(description="File to create or overwrite. Parent directories are created.")
    content: str = Field(description="Complete new content of the file.")


def _write_file_tool() -> RegisteredTool:
    def classify(args: WriteFileArgs, ctx: ToolContext) -> ActionInfo:
        return _write_action("write_file", resolve_path(args.path, ctx), ctx, "Write")

    async def run(args: WriteFileArgs, ctx: ToolContext, authorized: ActionInfo) -> ToolResult:
        path: Path = resolve_path(args.path, ctx)
        written: int = await asyncio.to_thread(atomic_write, path, args.content, journal_for(ctx, "write_file"))
        return ok(f"Wrote {written} bytes ({args.content.count(chr(10)) + 1} lines) to {path}")

    return make_path_tool(
        name="write_file",
        description=(
            "Create or overwrite a file with the given content. Prefer edit_file for small changes. The change goes "
            "into the undo journal: it shows in Changes and can be undone."
        ),
        args_model=WriteFileArgs,
        modes=ACTING_MODES,
        parallel_safe=False,
        timeout_s=60.0,
        classify=classify,
        run=run,
    )


class EditFileArgs(ToolArgs):
    path: str = Field(description="File to edit.")
    old: str = Field(description="Exact text to replace, including whitespace. Must be unique unless replace_all.")
    new: str = Field(description="Replacement text.")
    replace_all: bool = Field(default=False, description="Replace every occurrence instead of requiring uniqueness.")


def _closest_lines(text: str, needle: str) -> str:
    stripped: str = needle.strip()
    first: str = stripped.splitlines()[0] if stripped else ""
    candidates: list[str] = difflib.get_close_matches(
        first, [line.strip() for line in text.splitlines()], n=3, cutoff=0.6
    )
    return "; ".join(repr(candidate) for candidate in candidates)


def _crlf_only(text: str) -> bool:
    """Whether ``text`` breaks its lines, every one of them, with CRLF."""
    return "\r\n" in text and "\n" not in text.replace("\r\n", "")


def _with_crlf(snippet: str) -> str:
    """``snippet`` with its lines broken by CRLF."""
    return snippet.replace("\r\n", "\n").replace("\n", "\r\n")


def _edit_blocking(path: Path, old: str, new: str, replace_all: bool, journal: JournalTarget) -> str:
    """Replace ``old`` with ``new`` in the file and leave every other byte as it was: a file that breaks its lines with
    CRLF keeps them, and the old and the new text are taken with CRLF too (``read_file`` shows lines without it). Only
    UTF-8 text is edited; a file in another encoding is refused, saying so, rather than guessed at."""
    if not path.is_file():
        raise ToolExecutionError("file does not exist", {"path": str(path)})
    try:
        text: str = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ToolExecutionError(
            "the file is not UTF-8 text, the only kind edit_file changes; change it with the shell instead",
            {"path": str(path), "byte_offset": exc.start},
        ) from exc
    if _crlf_only(text):
        old, new = _with_crlf(old), _with_crlf(new)
    occurrences: int = text.count(old)
    if occurrences == 0:
        raise ToolExecutionError(
            "the text to replace was not found; re-read the file and copy it exactly",
            {"path": str(path), "closest_lines": _closest_lines(text, old)},
        )
    if occurrences > 1 and not replace_all:
        raise ToolExecutionError(
            f"the text occurs {occurrences} times; add surrounding context or set replace_all", {"path": str(path)}
        )
    updated: str = text.replace(old, new) if replace_all else text.replace(old, new, 1)
    atomic_write(path, updated, journal)
    return f"Edited {path}: replaced {occurrences if replace_all else 1} occurrence(s)"


def _edit_file_tool() -> RegisteredTool:
    def classify(args: EditFileArgs, ctx: ToolContext) -> ActionInfo:
        return _write_action("edit_file", resolve_path(args.path, ctx), ctx, "Edit")

    async def run(args: EditFileArgs, ctx: ToolContext, authorized: ActionInfo) -> ToolResult:
        message: str = await asyncio.to_thread(
            _edit_blocking,
            resolve_path(args.path, ctx),
            args.old,
            args.new,
            args.replace_all,
            journal_for(ctx, "edit_file"),
        )
        return ok(message)

    return make_path_tool(
        name="edit_file",
        description=(
            "Replace exact text in a file. Fails if the text is missing or ambiguous, so copy it verbatim. The change "
            "goes into the undo journal: it shows in Changes and can be undone."
        ),
        args_model=EditFileArgs,
        modes=ACTING_MODES,
        parallel_safe=False,
        timeout_s=60.0,
        classify=classify,
        run=run,
    )


class ListDirArgs(ToolArgs):
    path: str = Field(description="Directory to list.")
    depth: int | None = Field(default=None, ge=1, le=4, description="How many levels to descend (default 1).")


def _entry_size(entry: Path) -> int:
    try:
        return entry.stat().st_size
    except OSError:
        return 0


def _walk(root: Path, depth: int) -> list[str]:
    if not root.is_dir():
        raise ToolExecutionError("path is not a directory", {"path": str(root)})
    lines: list[str] = []

    def visit(directory: Path, level: int) -> None:
        try:
            entries: list[Path] = sorted(
                directory.iterdir(), key=lambda entry: (not entry.is_dir(), entry.name.lower())
            )
        except OSError as exc:
            lines.append(f"{'  ' * level}[unreadable: {exc.strerror}]")
            return
        for entry in entries:
            if len(lines) >= _LIST_LIMIT:
                return
            if entry.is_dir():
                lines.append(f"{'  ' * level}{entry.name}/")
                if level + 1 < depth and entry.name not in _SKIPPED_DIRS and not entry.is_symlink():
                    visit(entry, level + 1)
            else:
                lines.append(f"{'  ' * level}{entry.name}  ({_entry_size(entry)} B)")

    visit(root, 0)
    if len(lines) >= _LIST_LIMIT:
        lines.append(f"[listing capped at {_LIST_LIMIT} entries]")
    return lines


def _list_dir_tool() -> RegisteredTool:
    def classify(args: ListDirArgs, ctx: ToolContext) -> ActionInfo:
        return _read_action("list_dir", resolve_path(args.path, ctx), ctx, "List")

    async def run(args: ListDirArgs, ctx: ToolContext, authorized: ActionInfo) -> ToolResult:
        path: Path = resolve_path(args.path, ctx)
        lines: list[str] = await asyncio.to_thread(_walk, path, args.depth or 1)
        return ok(f"{path}\n" + ("\n".join(lines) if lines else "(empty)"))

    return make_path_tool(
        name="list_dir",
        description="List a directory (directories first). Use depth for a shallow tree.",
        args_model=ListDirArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=30.0,
        classify=classify,
        run=run,
    )


async def run_rg(
    arguments: list[str], confinement: tuple[str, ...], workspace: Path, env: dict[str, str]
) -> tuple[int | None, str, str]:
    """Run ripgrep in ``workspace``, started by ``confinement`` (the words of the run's sandbox, or none); returns
    ``(exit code, stdout, stderr)``."""
    executable: str | None = shutil.which("rg", path=env.get("PATH"))
    if executable is None:
        raise ToolExecutionError("ripgrep (rg) is not installed or not on PATH", {"hint": "brew install ripgrep"})
    process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
        *confinement,
        executable,
        *arguments,
        cwd=str(workspace),
        env=env,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await process.communicate()
    finally:
        # A tool timeout or a stopped run cancels the wait, not the search: end it so it stops walking the disk.
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
    return process.returncode, stdout.decode("utf-8", errors="replace"), stderr.decode("utf-8", errors="replace")


def _capped(lines: list[str], noun: str) -> str:
    shown: list[str] = lines[:_SEARCH_LINE_LIMIT]
    suffix: str = f"\n[{len(lines) - len(shown)} more {noun} not shown]" if len(lines) > len(shown) else ""
    return "\n".join(shown) + suffix


def _unread(stderr: str) -> list[str] | None:
    """What ripgrep said of the paths it did not read, because it was not allowed to (the sandbox of a run refuses Dot's
    own files and the protected locations, macOS the folders it guards) or because macOS broke off the reading, when
    that is all it said; None when it said anything else, such as a pattern it cannot read or a path that does not
    exist."""
    lines: list[str] = [line for line in stderr.splitlines() if line.strip() != ""]
    said: list[str] = [found.group(1) for line in lines if (found := _UNREAD_PATH.fullmatch(line)) is not None]
    return said if lines and len(said) == len(lines) else None


def _with_unread(found: str, unread: list[str]) -> str:
    """``found``, with a note of the paths that were not read, so that what they hold is known to be left out."""
    shown: list[str] = unread[:_UNREAD_SHOWN]
    more: str = f"\n[{len(unread) - len(shown)} more not shown]" if len(unread) > len(shown) else ""
    listed: str = "\n".join(shown)
    return f"{found}\n[Not read, so not in this answer ({len(unread)}):\n{listed}{more}]"


class SearchFilesArgs(ToolArgs):
    pattern: str = Field(description="Regular expression to search for in file contents.")
    path: str | None = Field(default=None, description="Directory or file to search. Defaults to the workspace.")
    glob: str | None = Field(default=None, description="Only search files matching this glob, e.g. '*.py'.")
    ignore_case: bool = Field(default=False, description="Case-insensitive match.")


def _search_files_tool() -> RegisteredTool:
    def target_of(args: SearchFilesArgs, ctx: ToolContext) -> Path:
        return resolve_path(args.path, ctx) if args.path else ctx.workspace

    def classify(args: SearchFilesArgs, ctx: ToolContext) -> ActionInfo:
        return _read_action("search_files", target_of(args, ctx), ctx, "Search in")

    async def run(args: SearchFilesArgs, ctx: ToolContext, authorized: ActionInfo) -> ToolResult:
        options: list[str] = ["--line-number", "--no-heading", "--color", "never", "--max-columns", "300"]
        if args.ignore_case:
            options.append("-i")
        if args.glob:
            options.extend(["-g", args.glob])
        code, stdout, stderr = await run_rg(
            [*options, "-e", args.pattern, "--", str(target_of(args, ctx))],
            run_sandbox(ctx).search,
            ctx.workspace,
            ctx.runtime.shell_env,
        )
        if code == 1:
            return ok("No matches.")
        lines: list[str] = stdout.splitlines()
        unread: list[str] | None = _unread(stderr) if code == _RG_ERROR else None
        found: ToolResult
        if unread is not None:
            found = ok(_with_unread(_capped(lines, "matches") if stdout.strip() != "" else "No matches.", unread))
        elif code != 0:
            raise ToolExecutionError("ripgrep failed", {"exit": code, "stderr": stderr[:500], "pattern": args.pattern})
        else:
            found = ok(_capped(lines, "matches"))
        # A match in a page a crawl kept or an MCP answer kept whole was written by third parties (item 494).
        return mark_untrusted(found) if matches_third_party(lines, target_of(args, ctx), ctx.workspace) else found

    return make_path_tool(
        name="search_files",
        description="Search file contents with ripgrep (regex). Returns path:line:text.",
        args_model=SearchFilesArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=60.0,
        classify=classify,
        run=run,
    )


class FindFilesArgs(ToolArgs):
    glob: str = Field(description="Glob such as '**/*.py' or '*.md'.")
    path: str | None = Field(default=None, description="Directory to search. Defaults to the workspace.")


def _find_files_tool() -> RegisteredTool:
    def target_of(args: FindFilesArgs, ctx: ToolContext) -> Path:
        return resolve_path(args.path, ctx) if args.path else ctx.workspace

    def classify(args: FindFilesArgs, ctx: ToolContext) -> ActionInfo:
        return _read_action("find_files", target_of(args, ctx), ctx, "Find files in")

    async def run(args: FindFilesArgs, ctx: ToolContext, authorized: ActionInfo) -> ToolResult:
        code, stdout, stderr = await run_rg(
            ["--files", "--hidden", "-g", args.glob, "--", str(target_of(args, ctx))],
            run_sandbox(ctx).search,
            ctx.workspace,
            ctx.runtime.shell_env,
        )
        if code == 1:
            return ok("No files matched.")
        unread: list[str] | None = _unread(stderr) if code == _RG_ERROR else None
        if unread is not None:
            found: str = _capped(sorted(stdout.splitlines()), "files") if stdout.strip() != "" else "No files matched."
            return ok(_with_unread(found, unread))
        if code != 0:
            raise ToolExecutionError("ripgrep failed", {"exit": code, "stderr": stderr[:500], "glob": args.glob})
        return ok(_capped(sorted(stdout.splitlines()), "files"))

    return make_path_tool(
        name="find_files",
        description="Find files by glob pattern (respects .gitignore).",
        args_model=FindFilesArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=60.0,
        classify=classify,
        run=run,
    )


def build_file_tools() -> tuple[RegisteredTool, ...]:
    return (
        _read_file_tool(),
        _write_file_tool(),
        _edit_file_tool(),
        _list_dir_tool(),
        _search_files_tool(),
        _find_files_tool(),
    )
