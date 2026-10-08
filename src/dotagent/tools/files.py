"""File tools: read, write, edit, list and search, with path-aware risk classification."""

import asyncio
import contextlib
import difflib
import os
import shutil
import tempfile
from itertools import islice
from pathlib import Path

from Foundation import NSURL
from pydantic import Field
from Quartz import PDFDocument

from dotagent.core.errors import ToolExecutionError
from dotagent.core.images import EncodedImage, encode_image
from dotagent.core.text import truncate_middle
from dotagent.policy.paths import PathClass, classify_path, expand_path
from dotagent.policy.types import ActionInfo, Flag, Risk
from dotagent.tools.base import (
    ACTING_MODES,
    ALL_MODES,
    RegisteredTool,
    ToolArgs,
    ToolResult,
    make_tool,
    ok,
    ok_with_images,
)
from dotagent.tools.context import ToolContext

_IMAGE_SUFFIXES: frozenset[str] = frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".heic"})
_DEFAULT_LINE_LIMIT: int = 2000
_MAX_LINE_CHARS: int = 2000
_LIST_LIMIT: int = 300
_SKIPPED_DIRS: frozenset[str] = frozenset(
    {".git", "node_modules", "__pycache__", ".venv", ".mypy_cache", ".ruff_cache"}
)
_SEARCH_LINE_LIMIT: int = 300


def resolve_path(raw: str, ctx: ToolContext) -> Path:
    return expand_path(raw, ctx.workspace)


def _read_action(tool: str, path: Path, ctx: ToolContext, verb: str) -> ActionInfo:
    path_class: PathClass = classify_path(path, ctx.workspace, ctx.runtime.protected)
    flags: tuple[Flag, ...] = ("secret_access",) if path_class == "protected" else ()
    return ActionInfo(tool=tool, kind="read", risk="safe", summary=f"{verb} {path}", target=str(path), flags=flags)


def _write_action(tool: str, path: Path, ctx: ToolContext, verb: str) -> ActionInfo:
    path_class: PathClass = classify_path(path, ctx.workspace, ctx.runtime.protected)
    risk: Risk
    flags: tuple[Flag, ...]
    if path_class in ("workspace", "temp"):
        risk, flags = "low", ()
    elif path_class == "home":
        risk, flags = "medium", ("outside_workspace",)
    elif path_class == "system":
        risk, flags = "high", ("system_settings", "outside_workspace")
    else:
        risk, flags = "high", ("secret_access",)
    return ActionInfo(tool=tool, kind="write", risk=risk, summary=f"{verb} {path}", target=str(path), flags=flags)


def _pdf_text(path: Path) -> str:
    document = PDFDocument.alloc().initWithURL_(NSURL.fileURLWithPath_(str(path)))
    if document is None:
        raise ToolExecutionError("the PDF could not be opened", {"path": str(path)})
    return str(document.string() or "")


def _is_binary(path: Path) -> bool:
    with path.open("rb") as handle:
        return b"\0" in handle.read(8192)


def _numbered_window(path: Path, offset: int, limit: int, budget: int) -> str:
    """Numbered lines starting at ``offset`` (1-based), cut by line count and by character budget."""
    shown: list[str] = []
    used: int = 0
    truncated: bool = False
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        window: list[str] = list(islice(handle, offset - 1, offset - 1 + limit))
        more: bool = next(handle, None) is not None
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
        text: str = _pdf_text(path)
        return ok(truncate_middle(text, budget) if text else "(the PDF contains no extractable text)")
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

    async def run(args: ReadFileArgs, ctx: ToolContext) -> ToolResult:
        desktop = ctx.runtime.config.desktop
        return await asyncio.to_thread(
            _read_blocking,
            resolve_path(args.path, ctx),
            args.offset or 1,
            args.limit or _DEFAULT_LINE_LIMIT,
            ctx.runtime.config.agent.tool_output_chars,
            desktop.screenshot_max_edge,
            desktop.jpeg_quality,
        )

    return make_tool(
        name="read_file",
        description="Read a text file with line numbers (also images and PDFs). Use offset/limit for large files.",
        args_model=ReadFileArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=60.0,
        classify=classify,
        run=run,
    )


def _atomic_write(path: Path, content: str) -> int:
    if path.is_dir():
        raise ToolExecutionError("path is a directory", {"path": str(path)})
    path.parent.mkdir(parents=True, exist_ok=True)
    mode: int | None = path.stat().st_mode & 0o777 if path.exists() else None
    handle, temp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(content)
        if mode is not None:
            os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    except OSError:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return len(content.encode("utf-8"))


class WriteFileArgs(ToolArgs):
    path: str = Field(description="File to create or overwrite. Parent directories are created.")
    content: str = Field(description="Complete new content of the file.")


def _write_file_tool() -> RegisteredTool:
    def classify(args: WriteFileArgs, ctx: ToolContext) -> ActionInfo:
        return _write_action("write_file", resolve_path(args.path, ctx), ctx, "Write")

    async def run(args: WriteFileArgs, ctx: ToolContext) -> ToolResult:
        path: Path = resolve_path(args.path, ctx)
        written: int = await asyncio.to_thread(_atomic_write, path, args.content)
        return ok(f"Wrote {written} bytes ({args.content.count(chr(10)) + 1} lines) to {path}")

    return make_tool(
        name="write_file",
        description="Create or overwrite a file with the given content. Prefer edit_file for small changes.",
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


def _edit_blocking(path: Path, old: str, new: str, replace_all: bool) -> str:
    if not path.is_file():
        raise ToolExecutionError("file does not exist", {"path": str(path)})
    text: str = path.read_text(encoding="utf-8")
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
    _atomic_write(path, updated)
    return f"Edited {path}: replaced {occurrences if replace_all else 1} occurrence(s)"


def _edit_file_tool() -> RegisteredTool:
    def classify(args: EditFileArgs, ctx: ToolContext) -> ActionInfo:
        return _write_action("edit_file", resolve_path(args.path, ctx), ctx, "Edit")

    async def run(args: EditFileArgs, ctx: ToolContext) -> ToolResult:
        message: str = await asyncio.to_thread(
            _edit_blocking, resolve_path(args.path, ctx), args.old, args.new, args.replace_all
        )
        return ok(message)

    return make_tool(
        name="edit_file",
        description="Replace exact text in a file. Fails if the text is missing or ambiguous, so copy it verbatim.",
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

    async def run(args: ListDirArgs, ctx: ToolContext) -> ToolResult:
        path: Path = resolve_path(args.path, ctx)
        lines: list[str] = await asyncio.to_thread(_walk, path, args.depth or 1)
        return ok(f"{path}\n" + ("\n".join(lines) if lines else "(empty)"))

    return make_tool(
        name="list_dir",
        description="List a directory (directories first). Use depth for a shallow tree.",
        args_model=ListDirArgs,
        modes=ALL_MODES,
        parallel_safe=True,
        timeout_s=30.0,
        classify=classify,
        run=run,
    )


async def run_rg(arguments: list[str], workspace: Path, env: dict[str, str]) -> tuple[int | None, str, str]:
    """Run ripgrep in ``workspace``; returns ``(exit code, stdout, stderr)``."""
    executable: str | None = shutil.which("rg", path=env.get("PATH"))
    if executable is None:
        raise ToolExecutionError("ripgrep (rg) is not installed or not on PATH", {"hint": "brew install ripgrep"})
    process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
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

    async def run(args: SearchFilesArgs, ctx: ToolContext) -> ToolResult:
        options: list[str] = ["--line-number", "--no-heading", "--color", "never", "--max-columns", "300"]
        if args.ignore_case:
            options.append("-i")
        if args.glob:
            options.extend(["-g", args.glob])
        code, stdout, stderr = await run_rg(
            [*options, "-e", args.pattern, "--", str(target_of(args, ctx))], ctx.workspace, ctx.runtime.shell_env
        )
        if code == 1:
            return ok("No matches.")
        if code != 0:
            raise ToolExecutionError("ripgrep failed", {"exit": code, "stderr": stderr[:500], "pattern": args.pattern})
        return ok(_capped(stdout.splitlines(), "matches"))

    return make_tool(
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

    async def run(args: FindFilesArgs, ctx: ToolContext) -> ToolResult:
        code, stdout, stderr = await run_rg(
            ["--files", "--hidden", "-g", args.glob, "--", str(target_of(args, ctx))],
            ctx.workspace,
            ctx.runtime.shell_env,
        )
        if code == 1:
            return ok("No files matched.")
        if code != 0:
            raise ToolExecutionError("ripgrep failed", {"exit": code, "stderr": stderr[:500], "glob": args.glob})
        return ok(_capped(sorted(stdout.splitlines()), "files"))

    return make_tool(
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
