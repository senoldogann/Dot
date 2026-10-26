"""The plugin scout's proposals on Customize › Plugins (item 712) through the API of a real in-process server: the
proposals and the refused are listed, Install puts a proposed plugin in Dot's plugins folder (the archive is made here:
no network) and the gallery lists it, Skip takes a proposal away, an update keeps the version it replaces and Revert
puts it back (the Trash is one made here), an update whose installed version moved is refused and changes nothing, and
"Look now" starts a night. No model is called.
"""

import asyncio
import io
import shutil
import tarfile
import time
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
from structlog.testing import capture_logs

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.errors import GalleryError
from dotagent.core.paths import failsafe_path
from dotagent.core.types import JsonObject
from dotagent.plugins import removal, scout_service
from dotagent.plugins.gallery import GalleryEntry, files_hash, unpack
from dotagent.plugins.scout import ScoutStopped
from dotagent.plugins.scout_types import INSTALL_GRACE_S, Candidate, Finding, Review, ScoutState
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server import routes_gallery, routes_mcp
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.skills.catalog import load_skills

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "plugin-scout-token"
OLD: str = "a" * 40
NEW: str = "b" * 40
FOUND: str = "c" * 40
THIRD: str = "d" * 40
_SKILL: str = "---\nname: {name}\ndescription: {does}\n---\n\nThe instructions.\n"


def _archive(path: Path, repository: str, commit: str, skills: dict[str, str]) -> None:
    with tarfile.open(path, "w:gz") as tar:
        for name, does in skills.items():
            data: bytes = _SKILL.format(name=name, does=does).encode()
            info: tarfile.TarInfo = tarfile.TarInfo(f"{repository.split('/')[1]}-{commit}/skills/{name}/SKILL.md")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))


def _entry(plugin_id: str, repository: str, commit: str, skill: str, files: str) -> GalleryEntry:
    return GalleryEntry(
        id=plugin_id,
        name=plugin_id.title(),
        publisher="Maker",
        official=True,
        category="development",
        does="Its skills.",
        repository=repository,
        commit=commit,
        skills=(f"skills/{skill}",),
        files=files,
        licence="MIT",
        stars=300,
        added=date(2026, 10, 1),
    )


def _hash(work: Path, archive: Path, entry: GalleryEntry) -> str:
    probe: Path = work / f"probe-{entry.id}-{entry.commit[:4]}"
    unpack(entry, archive, probe)
    return files_hash(probe / "skills")


def _candidate(
    plugin_id: str, kind: str, repository: str, commit: str, skill: str, files: str, **more: object
) -> Candidate:
    return Candidate(
        id=plugin_id,
        kind=kind,  # type: ignore[arg-type]
        name=plugin_id.title(),
        publisher="Maker",
        tier="a",
        source="Anthropic's official marketplace",
        category="business",
        repository=repository,
        commit=commit,
        skills=(f"skills/{skill}",),
        files=files,
        licence="MIT",
        stars=300,
        created=date(2025, 1, 1),
        pushed=date(2026, 10, 20),
        findings=(Finding(rule="script", severity="medium", path=f"{skill}/x.py", detail="1 script"),),
        review=Review(risk="low", refuse=False, does="Rapor yazar.", why="Sadece talimat.", model="ollama:r"),
        fit=0.8,
        found_at=1000.0,
        **more,  # type: ignore[arg-type]
    )


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def setup(
    tmp_path_factory: pytest.TempPathFactory,
) -> AsyncIterator[tuple[httpx.AsyncClient, Runtime, Path, Path, dict[str, str]]]:
    """A runtime in a throw-away home; the gallery is one plugin, ``deploy``, at its old commit; the archives of its old
    and new commits and of a plugin the scout found are made here. Yields the client, the runtime, Dot's plugins
    folder, the Trash and the hashes."""
    home: Path = tmp_path_factory.mktemp("home")
    trash: Path = tmp_path_factory.mktemp("trash")
    work: Path = tmp_path_factory.mktemp("work")
    archives: dict[str, Path] = {}
    for key, repository, commit, skill, does in (
        ("old", "maker/deploy", OLD, "deploy", "Deploy the site"),
        ("new", "maker/deploy", NEW, "deploy", "Deploy the site, with previews"),
        ("third", "maker/deploy", THIRD, "deploy", "Deploy the site, with previews and rollbacks"),
        ("found", "maker/reports", FOUND, "weekly-report", "Write the weekly report"),
    ):
        archives[f"{repository}@{commit}"] = work / f"{key}.tar.gz"
        _archive(archives[f"{repository}@{commit}"], repository, commit, {skill: does})
    hashes: dict[str, str] = {
        "old": _hash(work, archives[f"maker/deploy@{OLD}"], _entry("deploy", "maker/deploy", OLD, "deploy", "0" * 64)),
        "new": _hash(work, archives[f"maker/deploy@{NEW}"], _entry("deploy", "maker/deploy", NEW, "deploy", "0" * 64)),
        "third": _hash(
            work, archives[f"maker/deploy@{THIRD}"], _entry("deploy", "maker/deploy", THIRD, "deploy", "0" * 64)
        ),
        "found": _hash(
            work,
            archives[f"maker/reports@{FOUND}"],
            _entry("reports", "maker/reports", FOUND, "weekly-report", "0" * 64),
        ),
    }
    (home / "config.toml").write_text("[browser]\nheadless = true\n", encoding="utf-8")

    async def fetch(entry: GalleryEntry, folder: Path) -> Path:
        copy: Path = folder / "archive.tar.gz"
        await asyncio.to_thread(shutil.copyfile, archives[f"{entry.repository}@{entry.commit}"], copy)
        return copy

    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    patch.setenv("DOT_LOG_DIR", str(tmp_path_factory.mktemp("logs")))
    patch.setattr(routes_gallery, "GALLERY", (_entry("deploy", "maker/deploy", OLD, "deploy", hashes["old"]),))
    patch.setattr(routes_gallery, "download_archive", fetch)
    patch.setattr(removal, "trash_folder", lambda: trash)
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    manager: RunManager = RunManager(runtime)
    context: ServerContext = ServerContext(
        runtime=runtime,
        manager=manager,
        scheduler=None,
        token=TOKEN,
        host="127.0.0.1",
        port=8765,
        started_at=time.time(),
        stopping=asyncio.Event(),
    )
    transport: httpx.ASGITransport = httpx.ASGITransport(app=create_app(context))
    try:
        async with httpx.AsyncClient(
            transport=transport, base_url="http://127.0.0.1:8765", headers={"Authorization": f"Bearer {TOKEN}"}
        ) as client:
            yield client, runtime, home / "plugins", trash, hashes
    finally:
        await manager.shutdown()
        await close_runtime(runtime)
        patch.undo()


def _plugins(view: httpx.Response) -> dict[str, JsonObject]:
    body: JsonObject = view.raise_for_status().json()
    return {str(plugin["id"]): plugin for plugin in body["plugins"]}  # type: ignore[union-attr, index]


def _offered(runtime: Runtime) -> set[str]:
    return {skill.name for skill in load_skills(runtime.config.skills, runtime.plugins.plugins, None).skills}


async def test_a_proposal_is_listed_installed_and_joins_the_gallery_and_a_skipped_one_goes(
    setup: tuple[httpx.AsyncClient, Runtime, Path, Path, dict[str, str]],
) -> None:
    client, runtime, plugins, _trash, hashes = setup
    refused: Candidate = _candidate("leaky", "new", "maker/leaky", FOUND, "leak", "0" * 64).model_copy(
        update={
            "state": "refused",
            "review": Review(risk="high", refuse=True, does="Veri gönderir.", why="Toplama servisi.", model="r"),
        }
    )
    skipping: Candidate = _candidate("other", "new", "maker/other", FOUND, "other", "0" * 64)
    await scout_service.save(
        runtime.store,
        ScoutState(
            candidates=(
                _candidate("reports", "new", "maker/reports", FOUND, "weekly-report", hashes["found"]),
                refused,
                skipping,
            ),
            last_run=1000.0,
            last_summary="3 proposed",
        ),
    )
    view: JsonObject = (await client.get("/api/plugin-scout")).raise_for_status().json()
    assert [one["id"] for one in view["proposals"]] == ["reports", "other"]  # type: ignore[union-attr, index]
    assert [one["id"] for one in view["refused"]] == ["leaky"]  # type: ignore[union-attr, index]
    first: JsonObject = view["proposals"][0]  # type: ignore[index]
    assert (first["does"], first["risk"], first["fit"], first["skills"]) == (
        "Rapor yazar.",
        "low",
        0.8,
        ["weekly-report"],
    )
    assert view["last_summary"] == "3 proposed" and view["time"] == "03:30"

    installed: httpx.Response = await client.post("/api/plugin-scout/reports/install")
    assert installed.status_code == 200, installed.text
    assert [one["id"] for one in installed.json()["proposals"]] == ["other"]
    assert (plugins / "reports" / "skills" / "weekly-report" / "SKILL.md").is_file()
    assert "weekly-report" in _offered(runtime)
    listed: dict[str, JsonObject] = _plugins(await client.get("/api/plugin-gallery"))
    assert (listed["reports"]["state"], listed["reports"]["scouted"], listed["reports"]["does"]) == (
        "installed",
        True,
        "Rapor yazar.",
    )
    assert (await client.post("/api/plugin-scout/reports/install")).status_code == 404

    skipped: httpx.Response = await client.post("/api/plugin-scout/other/skip")
    assert skipped.status_code == 200 and skipped.json()["proposals"] == []
    state: ScoutState = await scout_service.load(runtime.store)
    assert {one.id: one.state for one in state.candidates} == {
        "reports": "installed",
        "leaky": "refused",
        "other": "skipped",
    }
    assert all(one.decided_at for one in state.candidates if one.id in ("reports", "other"))


async def test_an_update_keeps_the_version_it_replaces_and_revert_puts_it_back(
    setup: tuple[httpx.AsyncClient, Runtime, Path, Path, dict[str, str]],
) -> None:
    client, runtime, plugins, trash, hashes = setup
    assert (await client.post("/api/plugin-gallery/deploy")).status_code == 200
    update: Candidate = _candidate("deploy", "update", "maker/deploy", NEW, "deploy", hashes["new"], previous=OLD)
    await scout_service.save(runtime.store, ScoutState(candidates=(update,)))

    taken: httpx.Response = await client.post("/api/plugin-scout/deploy/install", params={"kind": "update"})
    assert taken.status_code == 200, taken.text
    assert "previews" in (plugins / "deploy" / "skills" / "deploy" / "SKILL.md").read_text(encoding="utf-8")
    assert (plugins / ".previous" / "deploy" / "skills" / "deploy" / "SKILL.md").is_file()
    after: JsonObject = _plugins(await client.get("/api/plugin-gallery"))["deploy"]
    assert (after["state"], after["installed_commit"], after["revertible"]) == ("updated", NEW, True)

    reverted: httpx.Response = await client.post("/api/plugin-gallery/deploy/revert")
    assert reverted.status_code == 200, reverted.text
    back: JsonObject = _plugins(reverted)["deploy"]
    assert (back["state"], back["installed_commit"], back["revertible"]) == ("installed", OLD, False)
    assert "previews" not in (plugins / "deploy" / "skills" / "deploy" / "SKILL.md").read_text(encoding="utf-8")
    assert "previews" in (trash / "deploy" / "skills" / "deploy" / "SKILL.md").read_text(encoding="utf-8")
    assert (await client.post("/api/plugin-gallery/deploy/revert")).status_code == 409
    state: ScoutState = await scout_service.load(runtime.store)
    assert [(one.id, one.kind, one.state) for one in state.candidates] == [("deploy", "update", "skipped")]


async def test_an_update_found_against_another_version_changes_nothing(
    setup: tuple[httpx.AsyncClient, Runtime, Path, Path, dict[str, str]],
) -> None:
    client, runtime, plugins, _trash, hashes = setup
    stale: Candidate = _candidate("deploy", "update", "maker/deploy", NEW, "deploy", hashes["new"], previous="9" * 40)
    await scout_service.save(runtime.store, ScoutState(candidates=(stale,)))
    before: str = (plugins / "deploy" / "skills" / "deploy" / "SKILL.md").read_text(encoding="utf-8")
    refused: httpx.Response = await client.post("/api/plugin-scout/deploy/install", params={"kind": "update"})
    assert refused.status_code == 409 and "not the version this update was found against" in refused.text
    assert (plugins / "deploy" / "skills" / "deploy" / "SKILL.md").read_text(encoding="utf-8") == before
    assert not (plugins / ".previous" / "deploy").exists()
    # An install that failed waits for the owner again, as it did before the click.
    state: ScoutState = await scout_service.load(runtime.store)
    assert [(one.id, one.state) for one in state.candidates] == [("deploy", "proposed")]
    assert (await client.post("/api/plugin-scout/nothing/install")).status_code == 404


async def test_look_now_starts_one_night_and_the_page_sees_it_run(
    setup: tuple[httpx.AsyncClient, Runtime, Path, Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    client, runtime, _plugins, _trash, _hashes = setup
    release: asyncio.Event = asyncio.Event()
    nights: list[float] = []
    lock: asyncio.Lock = scout_service._lock(runtime)

    async def night(given: Runtime, now: float) -> ScoutState:
        async with lock:
            nights.append(now)
            await release.wait()
            done: ScoutState = ScoutState(last_run=now, last_summary="0 proposed")
            await scout_service.save(given.store, done)
            return done

    monkeypatch.setattr(scout_service, "night", night)
    started: JsonObject = (await client.post("/api/plugin-scout/look")).raise_for_status().json()
    assert started["running"] is True
    again: JsonObject = (await client.post("/api/plugin-scout/look")).raise_for_status().json()
    assert again["running"] is True and len(nights) == 1
    release.set()
    async with asyncio.timeout(5):
        async with lock:
            pass
    assert (await client.get("/api/plugin-scout")).json()["running"] is False
    assert (await client.get("/api/plugin-scout")).json()["last_summary"] == "0 proposed"


async def test_an_install_made_while_a_night_looks_is_kept_when_the_night_ends(
    setup: tuple[httpx.AsyncClient, Runtime, Path, Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The review of the first push: a night stored the state it began with over the Install the owner made while it
    looked, and the proposal came back. The night's findings are laid over the state as it is when it ends."""
    client, runtime, plugins, _trash, hashes = setup
    waiting: Candidate = _candidate("racing", "new", "maker/reports", FOUND, "weekly-report", hashes["found"])
    await scout_service.save(runtime.store, ScoutState(candidates=(waiting,)))
    looking: asyncio.Event = asyncio.Event()
    release: asyncio.Event = asyncio.Event()

    async def night(deps: object, before: ScoutState) -> ScoutState:
        looking.set()
        await release.wait()
        found: Candidate = _candidate("brand-new", "new", "maker/other", FOUND, "other", "0" * 64).model_copy(
            update={"found_at": 2000.0}
        )
        return before.model_copy(update={"candidates": (found, *before.candidates), "last_run": 2000.0})

    monkeypatch.setattr(scout_service, "run_night", night)
    monkeypatch.setattr(scout_service, "optional_secret", lambda name: None)
    assert (await client.post("/api/plugin-scout/look")).status_code == 200
    await asyncio.wait_for(looking.wait(), 5)
    installed: httpx.Response = await client.post("/api/plugin-scout/racing/install")
    assert installed.status_code == 200, installed.text
    release.set()
    async with asyncio.timeout(5):
        async with scout_service._lock(runtime):
            pass
    state: ScoutState = await scout_service.load(runtime.store)
    assert {(one.id, one.state) for one in state.candidates} == {("brand-new", "proposed"), ("racing", "installed")}
    assert (plugins / "racing" / "skills" / "weekly-report" / "SKILL.md").is_file()


async def test_look_now_is_refused_while_the_emergency_stop_is_engaged(
    setup: tuple[httpx.AsyncClient, Runtime, Path, Path, dict[str, str]],
) -> None:
    client, runtime, _plugins, _trash, _hashes = setup
    stop: Path = failsafe_path()
    await asyncio.to_thread(stop.touch)
    try:
        refused: httpx.Response = await client.post("/api/plugin-scout/look")
    finally:
        await asyncio.to_thread(stop.unlink)
    assert refused.status_code == 409 and "emergency stop" in refused.text
    assert not scout_service.running(runtime)


async def test_look_now_twice_at_once_starts_one_night(
    setup: tuple[httpx.AsyncClient, Runtime, Path, Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The review of the second push: the second Look now came before the first night took its lock, and queued a
    second whole night."""
    client, _runtime, _plugins, _trash, _hashes = setup
    started: list[float] = []
    release: asyncio.Event = asyncio.Event()

    async def night(given: Runtime, now: float) -> ScoutState:
        started.append(now)
        await release.wait()
        return ScoutState(last_run=now)

    monkeypatch.setattr(scout_service, "night", night)
    first, second = await asyncio.gather(client.post("/api/plugin-scout/look"), client.post("/api/plugin-scout/look"))
    assert first.json()["running"] is True and second.json()["running"] is True
    await asyncio.sleep(0.05)
    assert len(started) == 1
    release.set()


async def test_a_proposal_being_installed_is_neither_skipped_nor_installed_twice(
    setup: tuple[httpx.AsyncClient, Runtime, Path, Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The review of the third push: Install read the proposal, downloaded and unpacked it, and only then marked it,
    so a Skip from another tab meanwhile was overwritten with an install the owner had skipped."""
    client, runtime, plugins, _trash, hashes = setup
    gated: Candidate = _candidate("gated", "new", "maker/reports", FOUND, "weekly-report", hashes["found"])
    await scout_service.save(runtime.store, ScoutState(candidates=(gated,)))
    fetching: asyncio.Event = asyncio.Event()
    release: asyncio.Event = asyncio.Event()
    fetch = routes_gallery.download_archive

    async def held(entry: GalleryEntry, folder: Path) -> Path:
        fetching.set()
        await release.wait()
        return await fetch(entry, folder)

    monkeypatch.setattr(routes_gallery, "download_archive", held)
    installing: asyncio.Task[httpx.Response] = asyncio.create_task(client.post("/api/plugin-scout/gated/install"))
    await asyncio.wait_for(fetching.wait(), 5)
    assert (await asyncio.wait_for(client.post("/api/plugin-scout/gated/skip"), 5)).status_code == 404
    assert (await asyncio.wait_for(client.post("/api/plugin-scout/gated/install"), 5)).status_code == 404
    assert (await client.get("/api/plugin-scout")).json()["proposals"] == []
    release.set()
    assert (await asyncio.wait_for(installing, 5)).status_code == 200
    state: ScoutState = await scout_service.load(runtime.store)
    assert [(one.id, one.state) for one in state.candidates] == [("gated", "installed")]
    assert (plugins / "gated" / "skills" / "weekly-report" / "SKILL.md").is_file()


async def test_an_install_left_off_by_a_daemon_that_died_waits_again(
    setup: tuple[httpx.AsyncClient, Runtime, Path, Path, dict[str, str]],
) -> None:
    client, runtime, _plugins, _trash, _hashes = setup
    now: float = time.time()
    left: Candidate = _candidate("left", "new", "maker/left", FOUND, "left", "0" * 64).model_copy(
        update={"state": "installing", "decided_at": now - INSTALL_GRACE_S - 1}
    )
    going: Candidate = _candidate("going", "new", "maker/going", FOUND, "going", "0" * 64).model_copy(
        update={"state": "installing", "decided_at": now}
    )
    await scout_service.save(runtime.store, ScoutState(candidates=(left, going)))
    view: JsonObject = (await client.get("/api/plugin-scout")).raise_for_status().json()
    assert [(one["id"], one["state"]) for one in view["proposals"]] == [("left", "proposed")]  # type: ignore[union-attr, index]
    assert (await client.post("/api/plugin-scout/going/skip")).status_code == 404
    assert (await client.post("/api/plugin-scout/left/skip")).status_code == 200
    state: ScoutState = await scout_service.load(runtime.store)
    assert [(one.id, one.state) for one in state.candidates] == [("left", "skipped"), ("going", "installing")]


async def test_revert_after_two_updates_skips_only_the_one_taken_back(
    setup: tuple[httpx.AsyncClient, Runtime, Path, Path, dict[str, str]],
) -> None:
    """The review of the third push: Revert marked every update of the plugin skipped, the one still installed too,
    and the gallery called the plugin outdated."""
    client, runtime, plugins, _trash, hashes = setup
    # The revert of the earlier test left the gallery's own version installed.
    assert _plugins(await client.get("/api/plugin-gallery"))["deploy"]["installed_commit"] == OLD
    first: Candidate = _candidate("deploy", "update", "maker/deploy", NEW, "deploy", hashes["new"], previous=OLD)
    await scout_service.save(runtime.store, ScoutState(candidates=(first,)))
    assert (await client.post("/api/plugin-scout/deploy/install", params={"kind": "update"})).status_code == 200
    second: Candidate = _candidate(
        "deploy", "update", "maker/deploy", THIRD, "deploy", hashes["third"], previous=NEW
    ).model_copy(update={"found_at": 2000.0})
    state: ScoutState = await scout_service.load(runtime.store)
    await scout_service.save(runtime.store, state.model_copy(update={"candidates": (second, *state.candidates)}))
    assert (await client.post("/api/plugin-scout/deploy/install", params={"kind": "update"})).status_code == 200
    assert "rollbacks" in (plugins / "deploy" / "skills" / "deploy" / "SKILL.md").read_text(encoding="utf-8")

    reverted: httpx.Response = await client.post("/api/plugin-gallery/deploy/revert")
    assert reverted.status_code == 200, reverted.text
    back: JsonObject = _plugins(reverted)["deploy"]
    assert (back["state"], back["installed_commit"]) == ("updated", NEW)
    state = await scout_service.load(runtime.store)
    assert [(one.commit, one.state) for one in state.candidates] == [(THIRD, "skipped"), (NEW, "installed")]


async def test_a_proposal_installed_and_removed_installs_again_from_the_gallery(
    setup: tuple[httpx.AsyncClient, Runtime, Path, Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The review of the fourth push: the gallery listed a plugin installed from a proposal, and once removed showed
    its Install, which answered 404 (only the gallery's own list was read) while the scout no longer proposed it."""
    client, runtime, plugins, trash, hashes = setup
    # Remove reads its own name of the Trash: the one of the test, never the owner's.
    monkeypatch.setattr(routes_mcp, "trash_folder", lambda: trash)
    again: Candidate = _candidate("again", "new", "maker/reports", FOUND, "weekly-report", hashes["found"])
    await scout_service.save(runtime.store, ScoutState(candidates=(again,)))
    assert (await client.post("/api/plugin-scout/again/install")).status_code == 200
    assert (await client.delete("/api/installed-plugins/again")).status_code == 200
    assert not (plugins / "again").exists() and (trash / "again").is_dir()
    assert _plugins(await client.get("/api/plugin-gallery"))["again"]["state"] == "available"
    installed: httpx.Response = await client.post("/api/plugin-gallery/again")
    assert installed.status_code == 200, installed.text
    assert _plugins(installed)["again"]["state"] == "installed"
    assert (plugins / "again" / "skills" / "weekly-report" / "SKILL.md").is_file()


async def test_revert_is_refused_for_a_version_installed_again_after_a_remove(
    setup: tuple[httpx.AsyncClient, Runtime, Path, Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The review of the sixth push: Remove left the version an update had set aside, and once the plugin was installed
    again from the gallery, Revert showed and would have put that version in place of the new install."""
    client, runtime, plugins, trash, hashes = setup
    monkeypatch.setattr(routes_mcp, "trash_folder", lambda: trash)
    if (plugins / "deploy").exists():
        assert (await client.delete("/api/installed-plugins/deploy")).status_code == 200
    assert (await client.post("/api/plugin-gallery/deploy")).status_code == 200
    update: Candidate = _candidate("deploy", "update", "maker/deploy", NEW, "deploy", hashes["new"], previous=OLD)
    await scout_service.save(runtime.store, ScoutState(candidates=(update,)))
    assert (await client.post("/api/plugin-scout/deploy/install", params={"kind": "update"})).status_code == 200
    assert _plugins(await client.get("/api/plugin-gallery"))["deploy"]["revertible"] is True
    assert (await client.delete("/api/installed-plugins/deploy")).status_code == 200
    assert (plugins / ".previous" / "deploy").is_dir()
    again: httpx.Response = await client.post("/api/plugin-gallery/deploy")
    assert again.status_code == 200, again.text
    assert (_plugins(again)["deploy"]["state"], _plugins(again)["deploy"]["revertible"]) == ("installed", False)
    refused: httpx.Response = await client.post("/api/plugin-gallery/deploy/revert")
    assert refused.status_code == 409 and "not an update Dot took" in refused.text
    assert "previews" not in (plugins / "deploy" / "skills" / "deploy" / "SKILL.md").read_text(encoding="utf-8")


async def test_an_emergency_stop_during_look_now_is_logged_as_a_stop_not_a_failure() -> None:
    async def stopped() -> ScoutState:
        raise ScoutStopped("the emergency stop is engaged: the look ended where it was", {})

    task: asyncio.Task[ScoutState] = asyncio.create_task(stopped())
    await asyncio.gather(task, return_exceptions=True)
    with capture_logs() as logs:
        routes_gallery._looked(task)
    assert [(one["event"], one["log_level"]) for one in logs] == [("scout_stopped", "info")]


async def test_a_revert_fails_the_update_proposed_from_the_version_it_took_back(
    setup: tuple[httpx.AsyncClient, Runtime, Path, Path, dict[str, str]],
) -> None:
    """The review of the ninth push: after a Revert, the update proposed from the version taken back stayed with an
    Update that answered 409 each time."""
    client, runtime, _plugins_dir, _trash, hashes = setup
    assert _plugins(await client.get("/api/plugin-gallery"))["deploy"]["installed_commit"] == OLD
    first: Candidate = _candidate("deploy", "update", "maker/deploy", NEW, "deploy", hashes["new"], previous=OLD)
    await scout_service.save(runtime.store, ScoutState(candidates=(first,)))
    assert (await client.post("/api/plugin-scout/deploy/install", params={"kind": "update"})).status_code == 200
    second: Candidate = _candidate(
        "deploy", "update", "maker/deploy", THIRD, "deploy", hashes["third"], previous=NEW
    ).model_copy(update={"found_at": 3000.0})
    state: ScoutState = await scout_service.load(runtime.store)
    await scout_service.save(runtime.store, state.model_copy(update={"candidates": (second, *state.candidates)}))
    assert (await client.post("/api/plugin-gallery/deploy/revert")).status_code == 200
    state = await scout_service.load(runtime.store)
    assert [(one.commit, one.state) for one in state.candidates] == [(THIRD, "failed"), (NEW, "skipped")]
    assert (await client.get("/api/plugin-scout")).json()["proposals"] == []


async def test_an_install_whose_plugins_are_not_read_again_stays_installed(
    setup: tuple[httpx.AsyncClient, Runtime, Path, Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The review of the ninth push: the files were in place, but a failure to read the plugins again put the proposal
    back as waiting, and its Install then answered that the name was taken."""
    client, runtime, plugins, _trash, hashes = setup
    unread: Candidate = _candidate("unread", "new", "maker/reports", FOUND, "weekly-report", hashes["found"])
    await scout_service.save(runtime.store, ScoutState(candidates=(unread,)))

    async def broken(*args: object) -> None:
        raise GalleryError("the plugins could not be read again", {})

    monkeypatch.setattr(routes_gallery, "read_again", broken)
    assert (await client.post("/api/plugin-scout/unread/install")).status_code == 409
    state: ScoutState = await scout_service.load(runtime.store)
    assert [(one.id, one.state) for one in state.candidates] == [("unread", "installed")]
    assert (plugins / "unread" / "skills" / "weekly-report" / "SKILL.md").is_file()


async def test_an_install_that_fails_after_another_tab_took_its_proposal_says_its_own_error(
    setup: tuple[httpx.AsyncClient, Runtime, Path, Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The review of the tenth push: putting the proposal back after a failed install raised 404 when another tab had
    taken it meanwhile, which hid the install's own error."""
    client, runtime, _plugins_dir, _trash, hashes = setup
    taken: Candidate = _candidate("taken", "new", "maker/reports", FOUND, "weekly-report", hashes["found"])
    await scout_service.save(runtime.store, ScoutState(candidates=(taken,)))
    fetching: asyncio.Event = asyncio.Event()
    release: asyncio.Event = asyncio.Event()

    async def held(entry: GalleryEntry, folder: Path) -> Path:
        fetching.set()
        await release.wait()
        raise GalleryError("GitHub answered 502 for the plugin's archive", {})

    monkeypatch.setattr(routes_gallery, "download_archive", held)
    installing: asyncio.Task[httpx.Response] = asyncio.create_task(client.post("/api/plugin-scout/taken/install"))
    await asyncio.wait_for(fetching.wait(), 5)
    await scout_service.save(runtime.store, ScoutState(candidates=(taken.model_copy(update={"state": "skipped"}),)))
    release.set()
    failed: httpx.Response = await asyncio.wait_for(installing, 5)
    assert failed.status_code == 409 and "502" in failed.text
    state: ScoutState = await scout_service.load(runtime.store)
    assert [(one.id, one.state) for one in state.candidates] == [("taken", "skipped")]


async def test_an_update_whose_plugin_was_removed_waits_for_nobody_and_the_night_retires_it(
    setup: tuple[httpx.AsyncClient, Runtime, Path, Path, dict[str, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The review of the twelfth push: only Revert retired an update proposed from a version no longer installed; after
    a Remove its Update answered 409 each time and the brief counted it each morning."""
    client, runtime, plugins, trash, hashes = setup
    monkeypatch.setattr(routes_mcp, "trash_folder", lambda: trash)
    if (plugins / "deploy").exists():
        assert (await client.delete("/api/installed-plugins/deploy")).status_code == 200
    assert (await client.post("/api/plugin-gallery/deploy")).status_code == 200
    update: Candidate = _candidate("deploy", "update", "maker/deploy", NEW, "deploy", hashes["new"], previous=OLD)
    await scout_service.save(runtime.store, ScoutState(candidates=(update,)))
    assert [one["id"] for one in (await client.get("/api/plugin-scout")).json()["proposals"]] == ["deploy"]
    assert (await client.delete("/api/installed-plugins/deploy")).status_code == 200
    assert (await client.get("/api/plugin-scout")).json()["proposals"] == []
    # Installed again at the version it was found against, it applies again.
    assert (await client.post("/api/plugin-gallery/deploy")).status_code == 200
    assert [one["id"] for one in (await client.get("/api/plugin-scout")).json()["proposals"]] == ["deploy"]
    assert (await client.delete("/api/installed-plugins/deploy")).status_code == 200

    async def night(deps: object, before: ScoutState) -> ScoutState:
        return before.model_copy(update={"last_run": 3000.0})

    monkeypatch.setattr(scout_service, "run_night", night)
    monkeypatch.setattr(scout_service, "optional_secret", lambda name: None)
    await scout_service.night(runtime, time.time())
    state: ScoutState = await scout_service.load(runtime.store)
    assert [(one.id, one.state) for one in state.candidates] == [("deploy", "failed")]
    assert (await client.post("/api/plugin-gallery/deploy")).status_code == 200
