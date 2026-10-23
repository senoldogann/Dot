"""What the desktop app's installer (item 575) takes from the Python package, kept equal on both sides: the providers it
offers are the ones ``dotx setup`` offers, the words it runs are ones ``dotx`` parses, the package it installs is this
package at the version of the app, and the files that decide who may call its commands are the ones reviewed. The
installer itself is Rust and is tested in ``desktop/src-tauri``; these tests read its files."""

import json
import plistlib
import re
import tomllib
from pathlib import Path

from dotagent import __version__
from dotagent.cli import build_parser, parse_command
from dotagent.cli_commands import ServiceInstallCommand, ServiceRestartCommand, SetupCommand
from dotagent.cli_setup import CHOICES, _key_name
from dotagent.config import load_default_config
from dotagent.dot_app import RUNTIME_FOLDER, app_path, app_program, runtime_path
from dotagent.launchd import LABEL, TOOLS_ENV, build_launch_agent, render_launch_agent

_ROOT: Path = Path(__file__).resolve().parents[2]
_DESKTOP: Path = _ROOT / "desktop"
_TAURI: Path = _DESKTOP / "src-tauri"
_INSTALLER: str = (_TAURI / "src" / "installer.rs").read_text(encoding="utf-8")


def _constant(name: str) -> str:
    found: re.Match[str] | None = re.search(rf'^const {name}: &str = "([^"]*)";$', _INSTALLER, re.MULTILINE)
    assert found is not None, name
    return found.group(1)


def test_the_providers_of_the_installer_are_the_ones_dotx_setup_offers() -> None:
    """The page lists what ``dotx setup`` offers (``CHOICES``), less OpenCode's own agent, which needs a program the
    app does not carry; the label, what each needs and whether it needs a key are the setup's own."""
    offered: list[dict[str, object]] = json.loads((_TAURI / "providers.json").read_text(encoding="utf-8"))
    config = load_default_config()
    by_id = {choice.provider: choice for choice in CHOICES}
    assert [entry["provider"] for entry in offered] == [
        choice.provider for choice in CHOICES if choice.provider != "opencode"
    ]
    for entry in offered:
        choice = by_id[str(entry["provider"])]
        assert (entry["label"], entry["needs"]) == (choice.label, choice.needs)
        assert entry["key"] is (_key_name(config, choice.provider) is not None), choice.provider
        assert isinstance(entry["models"], list) and all(isinstance(model, str) for model in entry["models"])


def test_the_words_the_installer_runs_are_ones_dotx_parses() -> None:
    """The installer builds these lines in ``installer.rs``; each is parsed by the real parser here, so a flag that is
    renamed in ``dotx`` is found before an owner's install is."""
    setup = parse_command(build_parser(), ["setup", "--model", "openai:gpt-5.5", "--no-service", "--key-stdin"])
    assert isinstance(setup, SetupCommand)
    assert (setup.model, setup.no_service, setup.key_stdin) == ("openai:gpt-5.5", True, True)
    keyless = parse_command(build_parser(), ["setup", "--model", "ollama:minimax-m2.7:cloud", "--no-service"])
    assert isinstance(keyless, SetupCommand) and not keyless.key_stdin
    service = parse_command(build_parser(), ["service", "install", "--launcher", "/x/dot-launcher"])
    assert isinstance(service, ServiceInstallCommand) and service.launcher == "/x/dot-launcher"
    restart = parse_command(build_parser(), ["service", "restart", "--when-idle"])
    assert isinstance(restart, ServiceRestartCommand) and restart.when_idle
    for word in ('"--model"', '"--no-service"', '"--key-stdin"', '"--launcher"', '"--when-idle"', '"--reinstall"'):
        assert word in _INSTALLER, word


def test_the_package_the_installer_installs_is_this_package_at_the_version_of_the_app() -> None:
    project = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert _constant("PACKAGE") == project["name"]
    assert project["requires-python"] == f">={_constant('PYTHON')}"
    # The app installs ``PACKAGE==<its own version>`` (``tauri.conf.json``), the version PyPI gets from the tag.
    conf = json.loads((_TAURI / "tauri.conf.json").read_text(encoding="utf-8"))
    assert conf["version"] == project["version"] == __version__
    assert _constant("LAUNCH_AGENT") == "dev.dotagent.dot.plist"
    # The app upgrades the package only when it can compare the two versions, which it reads as x.y.z (as a release
    # does: ``release.sh`` refuses another form), a pre-release suffix ranking below the same numbers without one.
    assert re.fullmatch(r"\d+\.\d+\.\d+", __version__), __version__


def test_the_runtime_folder_is_the_one_the_sandbox_keeps_commands_out_of() -> None:
    """The shell makes the folder under Dot's home that ``_own_code`` of the runtime names: what the app runs at its
    start (the Python of the package, the cache of what uv unpacked, the marker) is not a run's to change."""
    assert _constant("RUNTIME") == RUNTIME_FOLDER
    assert runtime_path(Path("/h")) == Path("/h") / _constant("RUNTIME")


def test_the_property_list_the_installer_reads_as_the_apps_own_job_is_what_the_renderer_writes() -> None:
    """The shell tells the app's own job from another Dot's by the property list's label and its first two program
    arguments (``is_the_apps_job`` in ``installer.rs``): Dot.app's program in Dot's home and the Python of the runtime
    folder's package environment. ``installer/app-job.plist`` is what the renderer writes for a home, and a Rust test
    reads it as the app's own job; this keeps it equal to what the renderer writes now, so that a change in the shape
    of the job is found here and not on an owner's Mac, where every start would read the app's own job as another's."""
    home: Path = Path("/Users/someone/Library/Application Support/Dot")
    logs: Path = Path("/Users/someone/Library/Logs/Dot")
    launcher: str = str(app_program(app_path(home)))
    python: str = str(runtime_path(home) / "uv-tools" / "dotagent-macos" / "bin" / "python")
    agent = build_launch_agent(launcher, python, home, logs, {TOOLS_ENV: str(runtime_path(home) / "app-tools")})
    fixture: bytes = (_TAURI / "src" / "installer" / "app-job.plist").read_bytes()
    assert render_launch_agent(agent) == fixture
    job: dict[str, object] = plistlib.loads(fixture)
    assert job["Label"] == LABEL == _constant("LABEL")
    assert _constant("LAUNCH_AGENT") == f"{LABEL}.plist"
    assert "Program" not in job
    words: object = job["ProgramArguments"]
    assert isinstance(words, list) and words[:2] == [launcher, python]
    # What follows is not compared by the shell, so that a later version may change it; a change is one to look at.
    assert words[2:] == ["-P", "-m", "dotagent", "daemon"]


def test_the_app_carries_its_tools_where_the_release_script_puts_them() -> None:
    conf = json.loads((_TAURI / "tauri.conf.json").read_text(encoding="utf-8"))
    # The three programs are named one by one, never as a glob: a file in the source folder is not in the app.
    assert conf["bundle"]["resources"] == {
        "resources/tools/uv": "tools/uv",
        "resources/tools/rg": "tools/rg",
        "resources/tools/dot-launcher": "tools/dot-launcher",
    }
    assert not any("*" in source for source in conf["bundle"]["resources"])
    build: str = (_TAURI / "build.rs").read_text(encoding="utf-8")
    assert 'const TOOLS: [&str; 3] = ["uv", "rg", "dot-launcher"];' in build
    fetch: str = (_DESKTOP / "fetch-tools.sh").read_text(encoding="utf-8")
    assert "out=$desktop/src-tauri/resources/tools" in fetch
    release: str = (_DESKTOP / "release.sh").read_text(encoding="utf-8")
    assert './fetch-tools.sh --sign "$identity"' in release
    assert "for tool in uv rg dot-launcher; do" in release
    assert '"$desktop/fetch-tools.sh" --check-bundle "$app/Contents/Resources/tools"' in release
    # The names the installer looks for in the app's Resources are the ones the script makes.
    for name in ("uv", "rg", "dot-launcher"):
        assert f'"{name}"' in _INSTALLER and name in fetch
    lock: str = (_DESKTOP / "tools.lock").read_text(encoding="utf-8")
    pinned = [line.split() for line in lock.splitlines() if line and not line.startswith("#")]
    assert sorted((line[0], line[2]) for line in pinned) == [
        ("rg", "aarch64"),
        ("rg", "x86_64"),
        ("uv", "aarch64"),
        ("uv", "x86_64"),
    ]
    for line in pinned:
        # tool, version, architecture, SHA-256 of the archive, its address, the program in it, SHA-256 of the program
        assert len(line) == 7, line
        assert re.fullmatch(r"[0-9a-f]{64}", line[3]) and line[4].startswith("https://"), line
        assert re.fullmatch(r"[0-9a-f]{64}", line[6]) and line[6] != line[3], line
        assert line[5].endswith(f"/{line[0]}"), line


def test_a_release_publishes_the_package_before_the_app() -> None:
    """The app installs its own version from PyPI, so the script ends with the tag first, a wait for PyPI, and the
    release last, made against the tag that exists (``--verify-tag``)."""
    release: str = (_DESKTOP / "release.sh").read_text(encoding="utf-8")
    tag: int = release.index("git tag $tag")
    wait: int = release.index("https://pypi.org/pypi/dotagent-macos/$version/json")
    made: int = release.index("gh release create $tag --verify-tag")
    assert tag < wait < made
    assert "--target" not in release[made - 200 :]


def test_the_installers_page_draws_what_it_is_told_as_text_and_loads_nothing_from_elsewhere() -> None:
    """A line a program printed, or an error text, comes to the page from the shell: it is drawn with ``textContent``,
    never as markup, and the page runs no code it builds and loads no script, style or font from an address."""
    ui: Path = _DESKTOP / "ui"
    for name in ("installer.js", "installer-model.js"):
        script: str = (ui / name).read_text(encoding="utf-8")
        for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function"):
            assert forbidden not in script, (name, forbidden)
        assert "fetch(" not in script and "XMLHttpRequest" not in script and "http" not in script.replace("//", ""), (
            name
        )
    page: str = (ui / "installer.html").read_text(encoding="utf-8")
    assert re.findall(r'(?:src|href)="([^"]+)"', page) == ["installer.css", "installer.js"]
    assert "<script>" not in page and "style=" not in page and "onclick" not in page


def test_only_the_capabilities_that_were_reviewed_are_there() -> None:
    """A capability file is what grants a page the shell's commands: a new one must be seen by a person."""
    names: list[str] = sorted(path.name for path in (_TAURI / "capabilities").glob("*.json"))
    assert names == ["default.json", "installer.json"]
