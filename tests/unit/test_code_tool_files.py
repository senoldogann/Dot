"""The tools of a run as files (item 1502): a typed function for each tool a code may call, made from its JSON schema.

The files are made and read as Python here with no process (compiled, read as syntax trees, and called with a stand-in
for the bridge); the ones that run in the sandbox, through the real executor, are the last tests. A description is third
parties' words for an MCP tool, so it is tried with every quote and escape that could end a string literal."""

import ast
import asyncio
import importlib
import json
import os
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from code_mode_stand_ins import Box, Run, box, run, tool

from dotagent.agent.executor import ToolOutcome
from dotagent.codemode import catalog, install, toolfiles
from dotagent.codemode.catalog import CatalogTool
from dotagent.codemode.sandbox import layout_for
from dotagent.core.types import JsonObject
from dotagent.llm.types import ToolSpec


def _spec(name: str, parameters: JsonObject | None = None, description: str = "does a thing") -> ToolSpec:
    schema: JsonObject = parameters if parameters is not None else {"type": "object", "properties": {}}
    return ToolSpec(name=name, description=description, parameters=schema)


def _signature(source: str, function: str) -> ast.FunctionDef:
    tree: ast.Module = ast.parse(source)
    (found,) = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == function]
    return found


def _arguments(node: ast.FunctionDef) -> dict[str, tuple[str, bool]]:
    """The keyword-only parameters of a function: each with its annotation and whether it has a default."""
    keyword: list[ast.arg] = node.args.kwonlyargs
    defaults: list[ast.expr | None] = list(node.args.kw_defaults)
    return {
        arg.arg: (ast.unparse(arg.annotation) if arg.annotation else "", default is not None)
        for arg, default in zip(keyword, defaults, strict=True)
    }


SCHEMA: JsonObject = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "The file to read."},
        "offset": {"type": "integer"},
        "ratio": {"type": "number"},
        "recursive": {"type": "boolean"},
        "tags": {"type": "array", "items": {"type": "string"}},
        "matrix": {"type": "array", "items": {"type": "array", "items": {"type": "integer"}}},
        "options": {"type": "object"},
        "mode": {"enum": ["fast", "slow", 3, None, True]},
        "either": {"anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]},
        "listed": {"type": ["string", "null"]},
        "fixed": {"const": "x"},
        "untyped": {},
        "weird": {"enum": [[1], {"a": 1}]},
        "bare_array": {"type": "array"},
        "from": {"type": "string"},
        "a-b": {"type": "string"},
        "2fa": {"type": "string"},
        "a_b": {"type": "string"},
        "_dot_call": {"type": "string"},
    },
    "required": ["path", "from"],
}


def test_a_schema_becomes_a_signature_of_keyword_only_parameters_with_the_types_it_names() -> None:
    (entry,) = catalog.build({"read_file": _spec("read_file", SCHEMA)})
    source: str = toolfiles.tool_source(entry)
    compile(source, "read_file.py", "exec")
    function: ast.FunctionDef = _signature(source, "read_file")
    assert function.args.args == [] and function.args.vararg is None and function.args.kwarg is None
    arguments: dict[str, tuple[str, bool]] = _arguments(function)
    assert arguments == {
        "path": ("str", False),
        "offset": ("int | None", True),
        "ratio": ("float | None", True),
        "recursive": ("bool | None", True),
        "tags": ("list[str] | None", True),
        "matrix": ("list[list[int]] | None", True),
        "options": ("dict[str, Any] | None", True),
        "mode": ("Literal['fast', 'slow', 3, None, True] | None", True),
        "either": ("str | int | None", True),
        "listed": ("str | None", True),
        "fixed": ("Literal['x'] | None", True),
        "untyped": ("Any", True),
        "weird": ("Any", True),
        "bare_array": ("list[Any] | None", True),
        "from_": ("str", False),
        "a_b": ("str | None", True),
        "_2fa": ("str | None", True),
        "a_b_": ("str | None", True),
        "_dot_call_": ("str | None", True),
    }
    # The keys a call carries are the tool's own, not the names Python needed.
    assert "'from': from_" in source and "'a-b': a_b" in source and "'2fa': _2fa" in source


def test_a_tool_without_parameters_and_one_that_takes_more_than_it_names_are_functions_too() -> None:
    built: dict[str, CatalogTool] = {
        entry.function: entry
        for entry in catalog.build(
            {
                "mcp__s__none": _spec("mcp__s__none"),
                "mcp__s__more": _spec(
                    "mcp__s__more", {"type": "object", "properties": {}, "additionalProperties": True}
                ),
                "mcp__s__plus": _spec(
                    "mcp__s__plus",
                    {
                        "type": "object",
                        "properties": {"a": {"type": "string"}},
                        "additionalProperties": {"type": "string"},
                    },
                ),
            }
        )
    }
    signatures: dict[str, str] = {
        name: ast.unparse(_signature(toolfiles.tool_source(entry), name).args) for name, entry in built.items()
    }
    assert signatures == {"none": "", "more": "**more: Any", "plus": "*, a: str | None=None, **more: Any"}


@pytest.mark.parametrize(
    "description",
    [
        "plain words",
        'a docstring that ends: """ and goes on',
        "it uses ''' and \"\"\" and ' and \"",
        "backslashes \\ \\n \\\\ \\x00 \\u1234 and a real newline\nsecond line\r\nthird",
        "unicode: åäö ğüşıİ 中文 \U0001f600   \u0085 and a null \x00 and an escape \x1b[31m",
        "braces {} {name} %s %(x)s $x ${y} and a comment # and code: import os; os.system('x')",
        "",
        "x" * 20000,
    ],
)
def test_a_description_is_the_docstring_and_never_code_however_it_is_written(description: str) -> None:
    (entry,) = catalog.build({"mcp__s__t": _spec("mcp__s__t", SCHEMA, description)})
    source: str = toolfiles.tool_source(entry)
    tree: ast.Module = ast.parse(source)
    function: ast.FunctionDef = _signature(source, "t")
    shown: str = ast.get_docstring(function, clean=False) or ""
    expected: str = description.strip()
    if len(expected) > toolfiles.DOC_CHARS:
        assert shown.startswith(expected[: toolfiles.DOC_CHARS]) and "dot_tools.search_tools" in shown
    else:
        assert shown.startswith(expected or "t")
    assert "Tool: mcp__s__t" in shown and "path: str, required. The file to read." in shown
    # Nothing of the description is a statement: the function holds the docstring, the arguments and the call.
    assert [type(statement).__name__ for statement in function.body] == ["Expr", "AnnAssign", "Return"]
    assert [type(statement).__name__ for statement in tree.body] == ["Expr", "ImportFrom", "ImportFrom", "FunctionDef"]


def _bridge(monkeypatch: pytest.MonkeyPatch, answer: Any = "done") -> list[tuple[str, dict[str, Any]]]:
    """A stand-in for ``dot_tools._bridge`` that records each call."""
    calls: list[tuple[str, dict[str, Any]]] = []
    stand_in: ModuleType = ModuleType("dot_tools._bridge")
    stand_in.call = lambda name, arguments: (calls.append((name, arguments)), answer)[1]  # type: ignore[attr-defined]
    package: ModuleType = ModuleType("dot_tools")
    package.__path__ = []  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "dot_tools", package)
    monkeypatch.setitem(sys.modules, "dot_tools._bridge", stand_in)
    return calls


def _load(entry: CatalogTool) -> Any:
    namespace: dict[str, Any] = {"__name__": f"dot_tools.servers.{entry.module}.{entry.function}"}
    exec(compile(toolfiles.tool_source(entry), f"{entry.function}.py", "exec"), namespace)
    return namespace[entry.function]


def test_a_function_calls_the_bridge_with_the_tools_name_and_only_the_arguments_it_was_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _bridge(monkeypatch, {"rows": [1]})
    (entry,) = catalog.build({"mcp__files__read_file": _spec("mcp__files__read_file", SCHEMA)})
    read_file: Any = _load(entry)
    assert read_file(path="a.txt", from_="x") == {"rows": [1]}
    assert read_file(path="b", from_="y", offset=0, recursive=False, tags=[]) == {"rows": [1]}
    assert calls == [
        ("mcp__files__read_file", {"path": "a.txt", "from": "x"}),
        ("mcp__files__read_file", {"path": "b", "from": "y", "offset": 0, "recursive": False, "tags": []}),
    ]
    # A parameter the tool requires is required of the call, and one it does not name is refused: where the code is.
    with pytest.raises(TypeError, match="required keyword-only argument"):
        read_file(path="a")
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        read_file(path="a", from_="x", surprise=1)
    with pytest.raises(TypeError):
        read_file("a.txt", "x")
    assert len(calls) == 2


def test_a_tool_that_takes_more_passes_what_it_was_given_beside_what_it_names(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _bridge(monkeypatch)
    schema: JsonObject = {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "additionalProperties": True,
    }
    (entry,) = catalog.build({"mcp__s__plus": _spec("mcp__s__plus", schema)})
    function: Any = _load(entry)
    function(a="1", other=2, third=None)
    function(other=2)
    assert calls == [("mcp__s__plus", {"other": 2, "a": "1"}), ("mcp__s__plus", {"other": 2})]


@pytest.fixture
def installed(tmp_path: Path) -> Iterator[tuple[Path, toolfiles.Package]]:
    """A package of tools written as the daemon writes it, on ``sys.path`` as the sandbox's launcher puts it."""
    layout = layout_for(tmp_path, "run_files")
    names: dict[str, ToolSpec] = {
        "read_file": _spec("read_file", SCHEMA, "Read a file."),
        "list_dir": _spec("list_dir"),
        "mcp__github__create_issue": _spec(
            "mcp__github__create_issue",
            {"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]},
        ),
        "mcp__github__class": _spec("mcp__github__class"),
        "mcp__web-research__web_scrape": _spec("mcp__web-research__web_scrape"),
    }
    package = toolfiles.package_of(names)
    install.install(layout, package.files)
    sys.path.insert(0, str(layout.tools))
    try:
        yield layout.tools, package
    finally:
        sys.path.remove(str(layout.tools))
        for name in [name for name in sys.modules if name == "dot_tools" or name.startswith("dot_tools.")]:
            del sys.modules[name]


def test_a_server_imports_a_tools_file_when_it_is_asked_for_and_gives_the_function_not_the_module(
    installed: tuple[Path, toolfiles.Package], monkeypatch: pytest.MonkeyPatch
) -> None:
    folder, package = installed
    calls: list[tuple[str, dict[str, Any]]] = []
    # The real files, as the daemon wrote them: only the bridge's own call is a stand-in.
    importlib.import_module("dot_tools")
    monkeypatch.setattr(
        sys.modules["dot_tools._bridge"], "call", lambda name, arguments: (calls.append((name, arguments)), "ok")[1]
    )
    servers: Any = importlib.import_module("dot_tools.servers.dot")
    assert sorted(dir(servers)) == ["list_dir", "read_file"]
    assert "dot_tools.servers.dot.read_file" not in sys.modules
    function: Any = servers.read_file
    assert callable(function) and not isinstance(function, ModuleType)
    assert function(path="a", from_="b") == "ok" and "dot_tools.servers.dot.read_file" in sys.modules
    assert servers.read_file is function and calls == [("read_file", {"path": "a", "from": "b"})]
    github: Any = importlib.import_module("dot_tools.servers.github")
    assert sorted(dir(github)) == ["class_", "create_issue"]
    assert github.class_() == "ok" and github.create_issue(title="x") == "ok"
    assert sorted(dir(importlib.import_module("dot_tools.servers.web_research"))) == ["web_scrape"]
    with pytest.raises(AttributeError, match=r"has no tool 'nope'.*search_tools"):
        servers.nope  # noqa: B018
    assert (folder / "dot_tools" / ".digest").read_text(encoding="ascii") == package.digest


def test_the_files_of_300_tools_are_made_fast_and_the_same_every_time(tmp_path: Path) -> None:
    specs: dict[str, ToolSpec] = {}
    for server in range(10):
        for number in range(30):
            name: str = f"mcp__server{server}__tool_{number}"
            properties: JsonObject = {
                f"param_{index}": {"type": "string", "description": "x" * 200} for index in range(8)
            }
            specs[name] = _spec(name, {"type": "object", "properties": properties, "required": ["param_0"]}, "d" * 2000)
    started: float = time.monotonic()
    package = toolfiles.package_of(specs)
    made_s: float = time.monotonic() - started
    assert len(specs) == 300 and len(package.files) == 300 + 10 + 3
    assert toolfiles.package_of(dict(reversed(list(specs.items())))).digest == package.digest
    layout = layout_for(tmp_path, "run_big")
    started = time.monotonic()
    install.install(layout, package.files)
    installed_s: float = time.monotonic() - started
    assert made_s < 10.0 and installed_s < 10.0
    size: int = sum(len(data) for data in package.files.values())
    assert size < 8_000_000
    assert json.loads(package.files[("dot_tools", "_catalog.json")])["servers"][0]["tools"] == 30


# ----- through the real executor and the real sandbox -----


@pytest.fixture
def code_box(tmp_path: Path) -> Box:
    return box(tmp_path)


def _exists(path: Path) -> bool:
    return path.exists()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _written(path: Path) -> int:
    return path.stat().st_mtime_ns


def _names(folder: Path) -> list[str]:
    return sorted(path.name for path in folder.iterdir() if path.is_dir())


def _tools(ran: list[JsonObject]) -> list[Any]:
    issue: JsonObject = {
        "type": "object",
        "properties": {"title": {"type": "string"}, "labels": {"type": "array", "items": {"type": "string"}}},
        "required": ["title"],
    }
    return [
        tool("read_file", '{"rows": [1, 2, 3]}', parameters=SCHEMA, ran=ran),
        tool("mcp__github__create_issue", "made", kind="mcp", parameters=issue, ran=ran),
        tool("mcp__github__class", "a class", kind="mcp", ran=ran),
        tool("mcp__web-research__web_scrape", "a page", kind="mcp_read", ran=ran),
    ]


@pytest.mark.macos
async def test_a_code_imports_its_tools_as_functions_and_calls_them_with_keywords(code_box: Box) -> None:
    ran: list[JsonObject] = []
    scene: Run = run(code_box, _tools(ran))
    outcome: ToolOutcome = await scene.code(
        "from dot_tools.servers.dot import read_file\n"
        "from dot_tools.servers.github import create_issue, class_\n"
        "from dot_tools.servers import web_research\n"
        "rows = read_file(path='a.txt', from_='x')['rows']\n"
        "print(len(rows), create_issue(title='bug', labels=['a', 'b']), class_(), web_research.web_scrape())\n"
        "import inspect\n"
        "print(list(inspect.signature(create_issue).parameters))\n"
        "print(read_file.__doc__.splitlines()[0], '|', read_file.__doc__.splitlines()[2])\n"
        "try:\n    create_issue()\nexcept TypeError as error:\n    print('TypeError:', error)\n"
        "try:\n    from dot_tools.servers.dot import finish\nexcept ImportError:\n    print('ImportError')\n"
    )
    assert not outcome.result.is_error, outcome.result.text
    lines: list[str] = outcome.result.text.splitlines()
    assert lines[0] == "3 made a class a page"
    assert lines[1] == "['title', 'labels']"
    assert lines[2] == "The stand-in read_file. | Tool: read_file"
    assert lines[3].startswith("TypeError: create_issue() missing 1 required keyword-only argument: 'title'")
    assert lines[4] == "ImportError"
    assert ran == [{"path": "a.txt", "from": "x"}, {"title": "bug", "labels": ["a", "b"]}, {}, {}]
    # The tools of the run are the files and nothing else: no tool that cannot be called from code has one.
    folder: Path = code_box.layout.tools / "dot_tools" / "servers"
    assert _names(folder) == ["dot", "github", "web_research"]
    assert not _exists(folder / "dot" / "code_run.py")


@pytest.mark.macos
async def test_the_files_are_written_once_for_the_same_tools_and_again_for_other_ones(code_box: Box) -> None:
    scene: Run = run(code_box, _tools([]))
    await scene.code("print(1)")
    file: Path = code_box.layout.tools / "dot_tools" / "servers" / "dot" / "read_file.py"
    marker: Path = code_box.layout.tools / "dot_tools" / ".digest"
    first: tuple[int, str] = (_written(file), _read(marker))
    await asyncio.sleep(0.05)
    await scene.code("print(2)")
    assert (_written(file), _read(marker)) == first
    # A static file is written before every start, whatever the tools: the launcher is the package's own.
    launcher: Path = code_box.layout.tools / "launcher.py"
    await asyncio.to_thread(launcher.write_text, "tampered", "utf-8")
    await scene.code("print(3)")
    assert "tampered" not in _read(launcher)
    other: Run = run(code_box, _tools([])[:2])
    await other.code("print(4)")
    assert _read(marker) != first[1]
    assert _exists(code_box.layout.tools / "dot_tools" / "servers" / "github" / "create_issue.py")
    shipped: JsonObject = json.loads(_read(code_box.layout.tools / "dot_tools" / "_catalog.json"))
    listed: list[str] = [str(entry["tool"]) for entry in shipped["tools"]]  # type: ignore[union-attr, index]
    assert listed == ["mcp__github__create_issue", "read_file"]
    # A tool the run is not given has no way in: it is not in the server's list, whatever an earlier file says.
    outcome: ToolOutcome = await other.code("from dot_tools.servers.github import class_\nprint(class_())")
    assert outcome.result.is_error and "ImportError" in outcome.result.text


def test_search_ranks_a_name_over_a_description_and_never_lists_more_than_25(
    installed: tuple[Path, toolfiles.Package],
) -> None:
    dot_tools: Any = importlib.import_module("dot_tools")
    assert dot_tools.search_tools("issue") == ["github.create_issue"]
    assert dot_tools.search_tools("read file") == ["dot.read_file"]
    entries: list[dict[str, Any]] = dot_tools.search_tools("scrape", detail="description")
    assert entries == [
        {
            "name": "web_research.web_scrape",
            "import": "from dot_tools.servers.web_research import web_scrape",
            "tool": "mcp__web-research__web_scrape",
            "description": "does a thing",
        }
    ]
    (schema,) = dot_tools.search_tools("create_issue", detail="schema")
    assert schema["parameters"]["required"] == ["title"] and schema["import"].endswith("import create_issue")
    assert [server["server"] for server in dot_tools.list_servers()] == ["dot", "github", "web_research"]
    assert {server["server"]: server["tools"] for server in dot_tools.list_servers()} == {
        "dot": 2,
        "github": 2,
        "web_research": 1,
    }
    with pytest.raises(ValueError, match="detail is one of name, description, schema"):
        dot_tools.search_tools("x", detail="everything")
    assert dot_tools.search_tools("zzzz nothing") == []
    # An empty search is the first tools, not all: the code is told how many it may have, never the whole list.
    assert len(dot_tools.search_tools("")) == 5 and len(dot_tools.search_tools("", limit=2)) == 2


def test_the_cap_of_a_search_holds_for_a_run_of_hundreds_of_tools(tmp_path: Path) -> None:
    specs: dict[str, ToolSpec] = {f"mcp__s{n % 7}__tool_{n}": _spec(f"mcp__s{n % 7}__tool_{n}") for n in range(300)}
    layout = layout_for(Path(os.path.realpath(tmp_path)), "run_search")
    install.install(layout, toolfiles.package_of(specs).files)
    sys.path.insert(0, str(layout.tools))
    try:
        dot_tools: Any = importlib.import_module("dot_tools")
        assert len(dot_tools.search_tools("tool", limit=1000)) == 25
        assert len(dot_tools.search_tools("", limit=1000)) == 25
        assert len(dot_tools.search_tools("tool_17")) >= 1 and dot_tools.search_tools("tool_17")[0].endswith("tool_17")
        assert sum(server["tools"] for server in dot_tools.list_servers()) == 300
    finally:
        sys.path.remove(str(layout.tools))
        for name in [name for name in sys.modules if name == "dot_tools" or name.startswith("dot_tools.")]:
            del sys.modules[name]


def test_the_definition_the_model_reads_is_the_same_whatever_the_run_has_to_call() -> None:
    from dotagent.codemode.tool import build_code_run_tools

    (entry,) = build_code_run_tools()
    before: str = entry.spec.model_dump_json()
    toolfiles.package_of({f"mcp__s__t{n}": _spec(f"mcp__s__t{n}", SCHEMA, "d" * 3000) for n in range(300)})
    assert entry.spec.model_dump_json() == before
    assert len(before) < 2600 and list(entry.spec.parameters["properties"]) == ["code", "title", "timeout_s"]  # type: ignore[call-overload]
