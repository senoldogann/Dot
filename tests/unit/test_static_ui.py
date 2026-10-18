"""The web UI is plain ES modules served as they are, with no build step: a wrong import shows only when a browser loads
the page, and the strict content security policy rules out inline code. These tests read the packaged files the way
the browser would."""

import re
from pathlib import Path

from dotagent.server.app import STATIC_DIR

JS_DIR: Path = STATIC_DIR / "js"
IMPORT: re.Pattern[str] = re.compile(r'^import\s+(?P<clause>[^;]*?)\s+from\s+"(?P<specifier>[^"]+)";', re.M | re.S)
NAMED: re.Pattern[str] = re.compile(r"^\{(?P<names>[^}]*)\}$", re.S)
NAMESPACE: re.Pattern[str] = re.compile(r"^\*\s+as\s+(?P<alias>\w+)$")
DECLARED_EXPORT: re.Pattern[str] = re.compile(r"^export\s+(?:async\s+)?(?:function\*?|const|let|class)\s+(\w+)", re.M)
LISTED_EXPORT: re.Pattern[str] = re.compile(r"^export\s*\{([^}]*)\}", re.M)
# What would let text become markup or code in the page.
FORBIDDEN_IN_SCRIPTS: tuple[str, ...] = (
    "innerHTML",
    "outerHTML",
    "insertAdjacentHTML",
    "document.write",
    "eval(",
    "new Function",
    "srcdoc",
    "javascript:",
)
FORBIDDEN_IN_PAGE: re.Pattern[str] = re.compile(r"<style\b|\sstyle\s*=|\son\w+\s*=|<form\b|<iframe\b", re.I)
SCRIPT_TAG: re.Pattern[str] = re.compile(r"<script\b(?P<attributes>[^>]*)>(?P<body>.*?)</script>", re.S)
REFERENCE: re.Pattern[str] = re.compile(r'(?:src|href)="([^"]+)"')
MODULE_ENTRY: re.Pattern[str] = re.compile(r'<script type="module" src="(?P<src>[^"]+)"></script>')
MODULEPRELOAD: re.Pattern[str] = re.compile(r'<link rel="modulepreload" href="(?P<place>[^"]+)">')


def read_scripts() -> dict[Path, str]:
    return {path.resolve(): path.read_text(encoding="utf-8") for path in sorted(JS_DIR.rglob("*.js"))}


def names_in(listed: str) -> list[tuple[str, str]]:
    """``(exported name, local name)`` of every entry of ``a, b as c``."""
    entries: list[list[str]] = [part.strip().split(" as ") for part in listed.split(",") if part.strip()]
    return [(entry[0].strip(), entry[-1].strip()) for entry in entries]


def exports_of(source: str) -> set[str]:
    names: set[str] = set(DECLARED_EXPORT.findall(source))
    for listed in LISTED_EXPORT.findall(source):
        names.update(exported for exported, _ in names_in(listed))
    return names


def reachable_modules(entry: Path, root: Path = STATIC_DIR) -> list[str]:
    """The places under ``root`` of the modules that ``entry`` imports, directly or through others, sorted as the page
    names them. ``entry`` itself is left out: the script tag is what loads it."""
    seen: set[Path] = set()
    pending: list[Path] = [entry.resolve()]
    while pending:
        file: Path = pending.pop()
        if file in seen:
            continue
        seen.add(file)
        for statement in IMPORT.finditer(file.read_text(encoding="utf-8")):
            specifier: str = statement["specifier"]
            if specifier.startswith("."):
                pending.append((file.parent / specifier).resolve())
    loaded: Path = entry.resolve()
    folder: Path = root.resolve()
    return sorted(path.relative_to(folder).as_posix() for path in seen if path != loaded)


def import_problems(file: Path, source: str, exports: dict[Path, set[str]]) -> list[str]:
    """What is wrong with the imports of one script: a file that is not there, a name its file does not export, a name
    that nothing in the script uses."""
    shown: str = str(file.relative_to(JS_DIR.resolve()))
    problems: list[str] = []
    for statement in IMPORT.finditer(source):
        specifier: str = statement["specifier"]
        target: Path = (file.parent / specifier).resolve()
        if not specifier.startswith(".") or target not in exports:
            problems.append(f"{shown}: {specifier} is not a script of the interface")
            continue
        clause: str = statement["clause"].strip()
        named: re.Match[str] | None = NAMED.match(clause)
        namespace: re.Match[str] | None = NAMESPACE.match(clause)
        imported: list[tuple[str, str]]
        if named is not None:
            imported = names_in(named["names"])
        elif namespace is not None:
            imported = [("*", namespace["alias"])]
        else:
            problems.append(f"{shown}: the import of {specifier} must name what it takes")
            continue
        body: str = source.replace(statement[0], "")
        for exported, local in imported:
            if exported != "*" and exported not in exports[target]:
                problems.append(f"{shown}: {specifier} does not export {exported}")
            if re.search(rf"\b{re.escape(local)}\b", body) is None:
                problems.append(f"{shown}: imports {local} and never uses it")
    return problems


def test_every_import_of_the_interface_names_an_export_of_a_script_that_exists() -> None:
    scripts: dict[Path, str] = read_scripts()
    assert (JS_DIR / "main.js").resolve() in scripts
    exports: dict[Path, set[str]] = {path: exports_of(source) for path, source in scripts.items()}
    problems: list[str] = [
        problem for path, source in scripts.items() for problem in import_problems(path, source, exports)
    ]
    assert problems == []


def test_the_scripts_never_turn_text_into_markup_or_code() -> None:
    found: list[str] = [
        f"{path.name} uses {word}"
        for path, source in read_scripts().items()
        for word in FORBIDDEN_IN_SCRIPTS
        if word in source
    ]
    assert found == []


def test_the_page_has_no_inline_code_and_loads_only_files_that_exist() -> None:
    page: str = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    scripts: list[re.Match[str]] = list(SCRIPT_TAG.finditer(page))
    assert [script["attributes"].strip() for script in scripts] == [
        'src="js/boot.js"',
        'type="module" src="js/main.js"',
    ]
    assert all(script["body"].strip() == "" for script in scripts)
    assert FORBIDDEN_IN_PAGE.search(page) is None
    missing: list[str] = [reference for reference in REFERENCE.findall(page) if not (STATIC_DIR / reference).is_file()]
    assert missing == []


def test_the_page_preloads_every_module_its_entry_reaches() -> None:
    """The page names every module its entry imports, one by one, before the entry itself is fetched: a link that is
    missing leaves a module to be found over the network where the page could have asked for it at once, and one that
    is stale asks for a file the entry no longer reaches."""
    page: str = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    entry: re.Match[str] | None = MODULE_ENTRY.search(page)
    assert entry is not None, "index.html loads no module of the interface"
    reachable: list[str] = reachable_modules(STATIC_DIR / entry["src"])
    listed: list[str] = [link["place"] for link in MODULEPRELOAD.finditer(page)]
    missing: list[str] = [place for place in reachable if place not in listed]
    stale: list[str] = [place for place in listed if place not in reachable]
    assert (missing, stale) == ([], []), (
        f"the modulepreload links miss {missing} and name {stale}, which {entry['src']} does not reach"
    )
    assert listed == reachable, (
        f"the modulepreload links must name each module {entry['src']} reaches once, sorted as this test sorts them"
    )
