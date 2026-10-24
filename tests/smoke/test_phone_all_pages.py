"""Pages of the web UI on a phone (item 436): no page sticks out sideways, no box is squeezed below its own words, no
text of a card's head is written across another's, and a list row spends its width on its words, with its buttons on
the line of its title. WebKit as the owner's iPhone, at 320, 360, 390 and 430 px, light and dark, over a served daemon
with something to show on each page.

The measures are the faults of the owner's own screens (Campaigns, Schedules and Customize, 2026-10-20), taken from
the page as it is drawn: a box wider than the window, a box of no width whose words spill out of it, a card's head whose
name the line beside it squeezed away, a row whose face or switch took a column and whose buttons then stood on a line
of their own. No network and no model service is used."""

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

import httpx
import pytest
from playwright.async_api import Browser, Page, async_playwright

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import ensure_layout
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.llm.router import parse_model_ref
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import ThreadRecord, VaultRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio]

TOKEN: str = "phone-pages-test-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_WIDTHS: tuple[int, ...] = (320, 360, 390, 430)
_SCHEMES: tuple[Literal["light", "dark"], ...] = ("light", "dark")
_CAMPAIGN_GOAL: str = "Collect 50 leads into leads.csv and keep a count of the ones that answered"
_CAMPAIGN_TITLE: str = "Find leads for the studio and write what each one answered"
_SCHEDULE_GOAL: str = "Read the inbox in the morning and list what needs an answer"
_TEAM_PURPOSE: str = "Reads what the studio's clients wrote, finds what each of them asked for and drafts the answers"

# What a person sees at a glance, measured on the drawn page. ``squeezed``: a box of no width whose words spill out of
# it. ``outside``: a box past the window's edge that no box scrolls. ``stranded``: a list row whose buttons stand on a
# line of their own under its words, with the words in a narrow column beside a lead.
_MEASURE: str = """() => {
  const vw = document.documentElement.clientWidth;
  const text = (e) => (e.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 40);
  const name = (e) => {
    const first = typeof e.className === 'string' ? e.className.trim().split(/\\s+/)[0] : '';
    return e.tagName.toLowerCase() + (first ? '.' + first : '');
  };
  const shown = (e) => {
    const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    if (s.visibility === 'hidden' || s.display === 'none' || r.height <= 0) return false;
    if (e.closest('.visually-hidden, [hidden]') || e.closest('details:not([open]) > :not(summary)')) return false;
    return r.width > 0 || (e.children.length === 0 && text(e) !== '');
  };
  const scrollsSideways = (e) => {
    for (let p = e.parentElement; p && p !== document.body; p = p.parentElement) {
      if (/(hidden|auto|scroll|clip)/.test(getComputedStyle(p).overflowX)) return true;
    }
    return false;
  };
  const found = [];
  for (const e of document.querySelectorAll('body *')) {
    if (!shown(e)) continue;
    const r = e.getBoundingClientRect(), s = getComputedStyle(e);
    const leaf = e.children.length === 0 && text(e) && s.overflowX === 'visible' && s.display !== 'inline';
    if (leaf && e.scrollWidth > e.clientWidth + 3 && e.clientWidth < 8) {
      found.push({ kind: 'squeezed', el: name(e), text: text(e) });
    }
    if (!scrollsSideways(e) && (r.right > vw + 1 || r.left < -1)) {
      found.push({ kind: 'outside', el: name(e), text: text(e), left: Math.round(r.left), right: Math.round(r.right) });
    }
  }
  for (const row of document.querySelectorAll('.list-row')) {
    const main = row.querySelector(':scope > .row-main'), act = row.querySelector(':scope > .row-actions');
    const title = row.querySelector('.row-title');
    if (!main || !act || !title || !shown(act)) continue;
    // the buttons are on the title's line, not under every other part of the row
    if (act.getBoundingClientRect().top > title.getBoundingClientRect().bottom + 4) {
      found.push({ kind: 'stranded', el: name(row), text: text(main) });
    }
  }
  return { vw, sideways: document.documentElement.scrollWidth - vw, found };
}"""


@pytest.fixture
def own_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home: Path = tmp_path / "home"
    monkeypatch.setenv("DOT_HOME", str(home))
    monkeypatch.setenv("DOT_CHROME_DIR", str(tmp_path / "chrome"))
    monkeypatch.setenv("DOT_LOG_DIR", str(tmp_path / "logs"))
    ensure_layout()
    return home


@asynccontextmanager
async def _served(home: Path) -> AsyncIterator[tuple[Runtime, int]]:
    """A daemon on the Dot home ``home`` whose main model is the scripted one, which no test here asks: its runtime and
    its port."""
    (home / "config.toml").write_text(
        f'[browser]\nheadless = true\n\n[models]\nmain = "{SCRIPTED_MODEL}"\n', encoding="utf-8"
    )
    runtime: Runtime = await build_runtime(load_config(home / "config.toml"))
    await runtime.router.register(_PROVIDER, ScriptedProvider(_PROVIDER, []))
    manager: RunManager = RunManager(runtime)
    try:
        stopping: asyncio.Event = asyncio.Event()
        listener = bind_listener("127.0.0.1", 0)
        port: int = listener.getsockname()[1]
        context: ServerContext = ServerContext(
            runtime=runtime,
            manager=manager,
            scheduler=None,
            token=TOKEN,
            host="127.0.0.1",
            port=port,
            started_at=time.time(),
            stopping=stopping,
        )
        server: DotServer = DotServer(serving_config(create_app(context)), stopping)
        serving: asyncio.Task[None] = asyncio.create_task(server.serve(sockets=[listener]))
        while not server.started:
            assert not serving.done(), "the server stopped while starting"
            await asyncio.sleep(0.05)
        try:
            yield runtime, port
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()
        await close_runtime(runtime)


async def _vault(runtime: Runtime) -> None:
    """Records of Dot's vault with long words (item 621): their rows only, as the page lists them; no secret is made, so
    no Keychain is asked."""
    now: float = time.time()
    long_site: str = "a-very-long-name-of-a-site-that-keeps-going.shop.example.org"
    await runtime.store.add_vault_items(
        [
            VaultRecord(
                id="vlt_a",
                kind="login",
                label=long_site,
                website=long_site,
                username="a.long.username.of.an.account@example.org",
                created_at=now,
                updated_at=now,
            ),
            VaultRecord(
                id="vlt_b",
                kind="card",
                label="Travel · Visa •••• 4242",
                brand="visa",
                last4="4242",
                nickname="Travel",
                fields={"city": "Izmir"},
                created_at=now,
                updated_at=now,
            ),
            VaultRecord(
                id="vlt_c",
                kind="card",
                label="American Express •••• 0005",
                brand="amex",
                last4="0005",
                created_at=now,
                updated_at=now,
            ),
            VaultRecord(
                id="vlt_d",
                kind="contact",
                label="Home of the whole family",
                fields={
                    "name": "Ada Augusta Lovelace of the long name",
                    "address": "12 Main Street, Flat 4, The Old Mill",
                    "city": "Izmir",
                },
                created_at=now,
                updated_at=now,
            ),
            VaultRecord(
                id="vlt_e", kind="login", label="example.com", website="example.com", created_at=now, updated_at=now
            ),
        ]
    )


async def _fill(runtime: Runtime, port: int) -> str:
    """Something to show: a campaign with a long title and goal, a schedule with options of its own, and a team of eight
    agents with long names and roles (item 610), and some of the vault's items; the team's id."""
    await _vault(runtime)
    thread: ThreadRecord = await runtime.store.create_thread(_CAMPAIGN_TITLE, "test")
    await runtime.store.create_campaign(
        _CAMPAIGN_TITLE, _CAMPAIGN_GOAL, thread.id, SCRIPTED_MODEL, "medium", "standard", None, "ask", (), "own"
    )
    async with httpx.AsyncClient(
        base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {TOKEN}"}
    ) as client:
        made: httpx.Response = await client.post(
            "/api/schedules",
            json={
                "name": "Read the inbox in the morning",
                "goal": _SCHEDULE_GOAL,
                "cron": "0 9 * * 1-5",
                "mode": "task",
                "model": SCRIPTED_MODEL,
                "effort": "high",
                "approvals": "yolo",
            },
        )
        assert made.status_code == 201, made.text
        ids: list[str] = []
        for number in range(1, 9):
            agent: httpx.Response = await client.post(
                "/api/agents",
                json={
                    "name": f"Researcher number {number} of the long names",
                    "role": "Finds the sources of a claim, reads them to the end and writes down what each one says",
                    "instructions": "",
                    "model": "",
                    "effort": None,
                    "approvals": None,
                    "tools": None,
                },
            )
            assert agent.status_code == 201, agent.text
            ids.append(agent.json()["id"])
        team: httpx.Response = await client.post(
            "/api/teams",
            json={"name": "The research desk of the whole studio", "purpose": _TEAM_PURPOSE, "agents": ids},
        )
        assert team.status_code == 201, team.text
        return str(team.json()["id"])


async def _page(browser: Browser, port: int, width: int, scheme: Literal["light", "dark"], route: str) -> Page:
    page: Page = await (
        await browser.new_context(
            viewport={"width": width, "height": 800},
            color_scheme=scheme,
            is_mobile=True,
            has_touch=True,
            reduced_motion="reduce",
        )
    ).new_page()
    await page.goto(ui_url("127.0.0.1", port, TOKEN))
    await page.goto(f"http://127.0.0.1:{port}/#{route}")
    await page.wait_for_selector(".page, .page-head, main", timeout=15_000)
    await page.wait_for_timeout(900)
    return page


# The pages with something on them that this daemon has: the owner's three that were broken, and the ones that share
# their heads and rows.
_ROUTES: tuple[str, ...] = (
    "/campaigns",
    "/schedules",
    "/customize/plugins",
    "/customize/connectors",
    "/agents",
    "/settings/memory",
    "/settings/vault",
    "/settings/appearance",
    "/settings/system",
    "/diagnostics",
)
# The pages whose looks at a phone's widths are kept as pictures for reading by eye.
_SHOWN: tuple[str, ...] = ("/campaigns", "/schedules", "/customize/plugins", "/customize/connectors", "/agents")


async def test_the_pages_fit_a_phone_and_their_heads_and_rows_keep_their_words(
    own_home: Path, tmp_path: Path, mac_at_rest: None
) -> None:
    async with _served(own_home) as (runtime, port), async_playwright() as playwright:
        team_id: str = await _fill(runtime, port)
        browser: Browser = await playwright.webkit.launch()
        try:
            seen: list[str] = []
            for width in _WIDTHS:
                for scheme in _SCHEMES:
                    for route in (*_ROUTES, f"/teams/{team_id}"):
                        page: Page = await _page(browser, port, width, scheme, route)
                        measured: dict[str, Any] = await page.evaluate(_MEASURE)
                        where: str = f"{route} at {width} {scheme}"
                        if measured["sideways"] > 0:
                            seen.append(f"{where}: the page scrolls sideways by {measured['sideways']} px")
                        seen.extend(f"{where}: {fault}" for fault in measured["found"])
                        if route in _SHOWN and width in (390, 430):
                            slug: str = route.strip("/").replace("/", "-")
                            await page.screenshot(
                                path=str(tmp_path / f"{slug}-{width}-{scheme}.png"), animations="disabled"
                            )
                        await page.context.close()
            assert seen == [], "\n".join(seen[:20])
        finally:
            await browser.close()


# Where a Popular card of Customize › Connectors draws its mark, its name and what it does, read from the page.
_POPULAR_TILES: str = """() => {
  const tiles = document.querySelectorAll('.plugin-section[data-section="popular"] .plugin-tile');
  return [...tiles].map((tile) => {
  const box = (element) => {
    const r = element.getBoundingClientRect();
    return { x: Math.round(r.x), right: Math.round(r.right), width: Math.round(r.width) };
  };
  return {
    name: tile.querySelector('.plugin-name').textContent,
    tile: box(tile),
    mark: box(tile.querySelector('.plugin-mark')),
    words: box(tile.querySelector('.plugin-name')),
    does: box(tile.querySelector('.plugin-does')),
  };
  });
}"""


async def test_the_popular_cards_are_rows_on_a_phone_and_keep_their_words_in_their_column(
    own_home: Path, mac_at_rest: None
) -> None:
    """Item 604: on the owner's iPhone the Popular cards of Customize › Connectors stood up as on a desk, a 48 px mark
    over words that a row's rules had put in a second column, beside an empty one, so a short line started halfway
    across the card. On a phone they are rows as the other sections' are, the words beside a mark of 40 px; on a desk
    they stand up, the words under the mark and from its edge."""
    async with _served(own_home) as (runtime, port), async_playwright() as playwright:
        await _fill(runtime, port)
        browser: Browser = await playwright.webkit.launch()
        try:
            for width in (390, 1280):
                page: Page = await _page(browser, port, width, "dark", "/customize/connectors")
                # The cards come with the catalogue, whose four lists include the MCP servers' state: a server slow to
                # start (web-research with camoufox, on a runner whose new programs macOS checks at their first start,
                # item 637) took it past the page's fixed wait, and the count was taken of none.
                await page.wait_for_selector('.plugin-section[data-section="popular"] .plugin-tile', timeout=30_000)
                tiles: list[dict[str, Any]] = await page.evaluate(_POPULAR_TILES)
                assert len(tiles) >= 4
                for tile in tiles:
                    where: str = f"{tile['name']} at {width}"
                    # The name and what it does start where each other does.
                    assert abs(tile["words"]["x"] - tile["does"]["x"]) <= 1, where
                    if width == 390:
                        assert tile["mark"]["width"] <= 40, where
                        assert tile["mark"]["right"] < tile["words"]["x"] <= tile["mark"]["right"] + 16, where
                    else:
                        assert abs(tile["words"]["x"] - tile["mark"]["x"]) <= 1, where
                await page.context.close()
        finally:
            await browser.close()
