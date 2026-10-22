"""The icon of an application of this Mac through the API of a real in-process server (Customize › Connectors).
Calendar, which every Mac has, answers its own icon as a PNG read from its bundle, and ``GET /api/connections`` says it
has one; an application of the web has none (404), nor has one that is not installed. OneDrive is found by the second of
the two bundle ids its builds go by, in a bundle made here, and its icon is that bundle's. Nothing outside this Mac is
reached and macOS is not asked about any application's Automation: the test answers for it.
"""

import asyncio
import io
import plistlib
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from PIL import Image

from dotagent.agent.manager import RunManager
from dotagent.config import AppConfig, load_default_config
from dotagent.connections import mac
from dotagent.desktop import perms
from dotagent.evals.runner import eval_config, eval_environment, scratch_home
from dotagent.runtime import Runtime, close_runtime, open_runtime
from dotagent.server import routes_connections
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext

pytestmark = pytest.mark.asyncio(loop_scope="module")

TOKEN: str = "app-icons-token"
_PORT: int = 8765
_PNG: bytes = b"\x89PNG\r\n\x1a\n"
_ONEDRIVE_MAC: str = "com.microsoft.OneDrive-mac"


@asynccontextmanager
async def _api(config: AppConfig) -> AsyncIterator[httpx.AsyncClient]:
    """A client of the API of a server opened on ``config``; the server is closed with it."""
    runtime: Runtime = await open_runtime(config, {})
    try:
        manager: RunManager = RunManager(runtime)
        context: ServerContext = ServerContext(
            runtime=runtime,
            manager=manager,
            scheduler=None,
            token=TOKEN,
            host="127.0.0.1",
            port=_PORT,
            started_at=time.time(),
            stopping=asyncio.Event(),
        )
        try:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=create_app(context)),
                base_url=f"http://127.0.0.1:{_PORT}",
                headers={"Authorization": f"Bearer {TOKEN}"},
            ) as client:
                yield client
        finally:
            await manager.shutdown()
    finally:
        await close_runtime(runtime)


def _onedrive_bundle(root: Path) -> Path:
    """An OneDrive bundle in ``root`` whose icon is a red square of 512 pixels."""
    bundle: Path = root / "OneDrive.app"
    resources: Path = bundle / "Contents" / "Resources"
    resources.mkdir(parents=True)
    (bundle / "Contents" / "Info.plist").write_bytes(
        plistlib.dumps({"CFBundleIdentifier": _ONEDRIVE_MAC, "CFBundleIconFile": "OneDrive"})
    )
    Image.new("RGBA", (512, 512), (200, 30, 30, 255)).save(resources / "OneDrive.icns", format="ICNS")
    return bundle


def _pixel(png: bytes) -> tuple[tuple[int, int], tuple[int, ...]]:
    """The size of a PNG and its middle pixel."""
    with Image.open(io.BytesIO(png)) as picture:
        rgba: Image.Image = picture.convert("RGBA")
        return rgba.size, rgba.getpixel((rgba.width // 2, rgba.height // 2))  # type: ignore[return-value]


async def test_the_api_serves_the_icon_of_an_installed_application_and_says_which_have_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mac, "automation_state", lambda bundle_id: "allowed")
    monkeypatch.setattr(perms, "responsible_program", lambda: Path("/Applications/Dot.app"))
    monkeypatch.setattr(routes_connections, "CLOUD_STORAGE", tmp_path / "CloudStorage")
    onedrive: Path = _onedrive_bundle(tmp_path)
    real_installed = mac.installed_app
    # Calendar is the Mac's own; OneDrive is the bundle made here, found by the second id only; nothing else is
    # installed for the test.
    monkeypatch.setattr(
        mac,
        "installed_app",
        lambda bundle_id: (
            onedrive
            if bundle_id == _ONEDRIVE_MAC
            else real_installed(bundle_id)
            if bundle_id == "com.apple.iCal"
            else None
        ),
    )
    with scratch_home() as home, eval_environment(home, {}):
        async with _api(eval_config(load_default_config())) as client:
            listed: httpx.Response = await client.get("/api/connections")
            calendar: httpx.Response = await client.get("/api/connections/calendar/icon")
            folder: httpx.Response = await client.get("/api/connections/onedrive/icon")
            not_installed: httpx.Response = await client.get("/api/connections/mail/icon")
            web: httpx.Response = await client.get("/api/connections/google/icon")
            unknown: httpx.Response = await client.get("/api/connections/nowhere/icon")
    assert listed.status_code == 200, listed.text
    icons: dict[str, bool] = {app["id"]: app["icon"] for app in listed.json()["apps"]}
    assert icons["calendar"] and icons["onedrive"]
    assert not any(icons[name] for name in ("mail", "outlook", "google", "github", "slack"))
    states: dict[str, str] = {app["id"]: app["state"] for app in listed.json()["apps"]}
    assert (states["calendar"], states["onedrive"], states["mail"]) == ("allowed", "not_synced", "not_installed")
    assert calendar.status_code == 200 and calendar.headers["content-type"] == "image/png"
    assert calendar.content.startswith(_PNG)
    size, _ = _pixel(calendar.content)
    assert size[0] == size[1] and 128 <= size[0] <= 256
    assert folder.status_code == 200 and folder.content.startswith(_PNG)
    assert _pixel(folder.content) == ((256, 256), (200, 30, 30, 255))
    for refused in (not_installed, web, unknown):
        assert refused.status_code == 404, refused.text
    assert "not installed" in not_installed.json()["message"]
