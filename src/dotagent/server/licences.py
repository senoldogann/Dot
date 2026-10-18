"""What Settings › System › Open source licences shows (item 371), read from what is really there: the Python packages
installed beside the daemon with the licence their metadata names, the notices that come with Dot's pages (fonts,
icons, and the code taken from other projects, whose copyright the MIT licence asks Dot to keep), and the tools Dot
uses when they are on this Mac, found or not."""

import shutil
from collections.abc import Sequence
from importlib.metadata import Distribution, distributions
from pathlib import Path
from urllib.parse import urlsplit

from dotagent.core.types import FrozenModel
from dotagent.server.diagnostics import shown_path

# Where the pages and their notices are, beside this module.
_PAGES: Path = Path(__file__).resolve().parent / "static"
# The labels of a Project-URL that name the page a package is known by.
_HOME_LABELS: frozenset[str] = frozenset({"homepage", "home", "source", "source code", "repository", "github"})
# A licence field that holds the whole text gives its first line, cut to this many characters.
_LICENCE_CHARS: int = 80


class PackageLicence(FrozenModel):
    """A Python package installed beside the daemon: its version, the licence its metadata names ("" when it names
    none) and the page it is known by ("" when it gives none)."""

    name: str
    version: str
    licence: str
    url: str


class BundledNotice(FrozenModel):
    """A notice that comes with Dot's pages: what it covers, where it is under the pages, and its words."""

    name: str
    path: str
    text: str


class ToolLicence(FrozenModel):
    """A tool Dot uses when it is there: what Dot uses it for, where it is on this Mac ("" when it is not), its
    licence and the page that holds it."""

    name: str
    does: str
    where: str
    licence: str
    url: str


class LicencesView(FrozenModel):
    packages: tuple[PackageLicence, ...]
    bundled: tuple[BundledNotice, ...]
    tools: tuple[ToolLicence, ...]


class _Tool(FrozenModel):
    """A tool Dot uses, and how it is found: a command on the owner's PATH or an app in /Applications."""

    name: str
    does: str
    command: str | None
    app: str | None
    licence: str
    url: str


_TOOLS: tuple[_Tool, ...] = (
    _Tool(
        name="Google Chrome",
        does="the browser of Dot's runs",
        command=None,
        app="/Applications/Google Chrome.app",
        licence="Google Chrome Terms of Service",
        url="https://www.google.com/chrome/terms/",
    ),
    _Tool(
        name="Ollama",
        does="local models, Ollama's cloud models and its web search",
        command="ollama",
        app="/Applications/Ollama.app",
        licence="MIT",
        url="https://github.com/ollama/ollama/blob/main/LICENSE",
    ),
    _Tool(
        name="Node.js",
        does="npx, which starts many MCP servers",
        command="node",
        app=None,
        licence="MIT",
        url="https://github.com/nodejs/node/blob/main/LICENSE",
    ),
    _Tool(
        name="Tailscale",
        does="the way a phone reaches this Mac",
        command="tailscale",
        app="/Applications/Tailscale.app",
        licence="BSD-3-Clause",
        url="https://github.com/tailscale/tailscale/blob/main/LICENSE",
    ),
    _Tool(
        name="OpenCode",
        does="the runner of OpenCode's models",
        command="opencode",
        app=None,
        licence="MIT",
        url="https://github.com/sst/opencode/blob/dev/LICENSE",
    ),
)


def licence_of(expression: str | None, field: str | None, classifiers: Sequence[str]) -> str:
    """The licence a package's metadata names: its SPDX expression, else its License field (the first line of a field
    that holds the whole text), else the licences its classifiers name; "" when it names none."""
    if expression is not None and expression.strip() != "":
        return expression.strip()
    if field is not None and field.strip() not in {"", "UNKNOWN"}:
        return field.strip().splitlines()[0].strip()[:_LICENCE_CHARS]
    named: list[str] = [entry.split(" :: ")[-1] for entry in classifiers if entry.startswith("License :: ")]
    return ", ".join(dict.fromkeys(named))


def _web(address: str) -> bool:
    """Whether ``address`` is a web page's, http or https: a package's metadata may name anything."""
    parts = urlsplit(address)
    return parts.scheme in {"http", "https"} and parts.netloc != ""


def url_of(home_page: str | None, project_urls: Sequence[str]) -> str:
    """The web page a package's metadata points at: its Home-page, else its first Project-URL whose label names a home,
    a source or a repository, else its first Project-URL; only an http or https address, "" when it gives none."""
    if home_page is not None and _web(home_page.strip()):
        return home_page.strip()
    pairs: list[tuple[str, str]] = [
        (label.strip().lower(), address.strip())
        for label, _, address in (entry.partition(",") for entry in project_urls)
        if _web(address.strip())
    ]
    return next((address for label, address in pairs if label in _HOME_LABELS), pairs[0][1] if pairs else "")


def _package(dist: Distribution) -> PackageLicence:
    return PackageLicence(
        name=dist.metadata["Name"],
        version=dist.version,
        licence=licence_of(
            dist.metadata.get("License-Expression"),
            dist.metadata.get("License"),
            dist.metadata.get_all("Classifier") or [],
        ),
        url=url_of(dist.metadata.get("Home-page"), dist.metadata.get_all("Project-URL") or []),
    )


def installed_packages() -> tuple[PackageLicence, ...]:
    """The packages installed beside the daemon, by name, each once."""
    found: dict[str, PackageLicence] = {}
    for dist in distributions():
        package: PackageLicence = _package(dist)
        found.setdefault(package.name.lower(), package)
    return tuple(found[key] for key in sorted(found))


def notice_name(relative: Path) -> str:
    """What a notice of the pages covers, from where it is: what follows ``LICENSE-`` in its file's name (a font, the
    icons), else its folder under the pages."""
    if relative.name.upper().startswith("LICENSE-"):
        return Path(relative.name[len("LICENSE-") :]).stem
    return relative.parent.as_posix()


def bundled_notices(pages: Path) -> tuple[BundledNotice, ...]:
    """The notices under ``pages``, by where they are."""
    return tuple(
        BundledNotice(
            name=notice_name(path.relative_to(pages)),
            path=path.relative_to(pages).as_posix(),
            text=path.read_text(encoding="utf-8"),
        )
        for path in sorted(pages.rglob("LICENSE*"))
        if path.is_file()
    )


def _where(tool: _Tool, search_path: str) -> str:
    """Where ``tool`` is on this Mac: its command on ``search_path``, else its app; "" when neither is there."""
    command: str | None = None if tool.command is None else shutil.which(tool.command, path=search_path)
    if command is not None:
        return shown_path(Path(command))
    if tool.app is not None and Path(tool.app).is_dir():
        return tool.app
    return ""


def read_licences(search_path: str) -> LicencesView:
    """Every licence the page shows; ``search_path`` is the PATH of the owner's shell, where a tool's command is
    looked for."""
    return LicencesView(
        packages=installed_packages(),
        bundled=bundled_notices(_PAGES),
        tools=tuple(
            ToolLicence(
                name=tool.name, does=tool.does, where=_where(tool, search_path), licence=tool.licence, url=tool.url
            )
            for tool in _TOOLS
        ),
    )
