"""The context size of a model's runs (item 250): Settings › Models offers, beside each model's window, the sizes its
window allows, Dot's default first; a choice goes into the config file with the rest of it as it was and holds from the
model's next run, with no restart; a size below the floor or past three quarters of the window is refused, and nothing
changes. Why opens a row that says what the size does, where the window comes from, and what the model's runs of the
last 30 days measured. A real server whose providers list the test's models and answer each call with a few words, its
run history seeded; wide and on a phone, light and dark."""

import asyncio
import os
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import httpx
import pytest
import pytest_asyncio
from playwright.async_api import Browser, Locator, Page, async_playwright, expect

from dotagent.agent.manager import RunManager
from dotagent.config import load_config
from dotagent.core.paths import config_path
from dotagent.core.types import JsonObject
from dotagent.evals.runner import SCRIPTED_MODEL
from dotagent.evals.scripted import ScriptedProvider
from dotagent.evals.types import ScriptedTurn
from dotagent.llm.calls import CALL_EVENT
from dotagent.llm.router import parse_model_ref
from dotagent.llm.types import ChatRequest, DeltaSink, ModelInfo, ModelReply, WindowSource
from dotagent.runtime import Runtime, build_runtime, close_runtime
from dotagent.server.app import create_app
from dotagent.server.context import ServerContext
from dotagent.server.daemon import DotServer, bind_listener, serving_config
from dotagent.server.links import ui_url
from dotagent.store.models import WORKING_STATUSES, EventRecord, RunRecord, ThreadRecord

pytestmark = [pytest.mark.browser, pytest.mark.asyncio(loop_scope="module")]

TOKEN: str = "context-sizes-token"
_PROVIDER: str = parse_model_ref(SCRIPTED_MODEL).provider
_SHOTS: tuple[tuple[int, Literal["light", "dark"]], ...] = (
    (1280, "light"),
    (1280, "dark"),
    (390, "light"),
    (390, "dark"),
)
# A model whose window Dot asks Ollama for, under its own, and one whose window holds no size from the floor up.
_ASKED: str = f"{_PROVIDER}:qwen3-coder"
_TINY: str = f"{_PROVIDER}:tinyllama"
_OPUS: str = "anthropic:claude-opus-5-5"
# The owner's own words in the file, which a choice must leave as they are.
_MODELS_TABLE: str = (
    f'[models]\nmain = "{SCRIPTED_MODEL}"\n# mine: keep this\nfast = "{SCRIPTED_MODEL}"\n'
    f'review = "{SCRIPTED_MODEL}"\nvision = "{_OPUS}"\n'
)
_SIZES: list[str] = ["", "48000", "64000", "128000", "192000", "256000", "384000", "512000", "750000"]
_SIZE_TEXTS: list[str] = ["96k (default)", "48k", "64k", "128k", "192k", "256k", "384k", "512k", "750k (most)"]


def _model(name: str, window: int, own: int | None, source: WindowSource, vision: bool) -> ModelInfo:
    return ModelInfo(
        id=name,
        vision=vision,
        tools=True,
        thinking=False,
        context_length=window,
        own_context_length=own,
        window_source=source,
        efforts=(),
        free=False,
    )


class Listed:
    """A provider that lists the test's models and answers each call with a few words, which ends a chat's run."""

    def __init__(self, name: str, models: tuple[ModelInfo, ...]) -> None:
        self._name: str = name
        self._models: tuple[ModelInfo, ...] = models

    @property
    def name(self) -> str:
        return self._name

    async def complete(self, request: ChatRequest, sink: DeltaSink) -> ModelReply:
        return await ScriptedProvider(self._name, [ScriptedTurn(text="Done.", calls=())]).complete(request, sink)

    async def model_info(self, model: str) -> ModelInfo:
        return next(info for info in self._models if info.id == model)

    async def list_models(self) -> tuple[ModelInfo, ...]:
        return self._models

    async def aclose(self) -> None:
        return None


_LISTS: dict[str, tuple[ModelInfo, ...]] = {
    _PROVIDER: (
        _model(parse_model_ref(SCRIPTED_MODEL).model, 1_000_000, None, "provider", False),
        _model("qwen3-coder", 65_536, 262_144, "asked", False),
        _model("tinyllama", 32_768, None, "provider", False),
    ),
    "anthropic": (_model("claude-opus-5-5", 1_000_000, None, "provider", True),),
}


async def _seed_history(runtime: Runtime) -> None:
    """A finished run of the scripted model with three steps that answered (their input 20,000, 40,000 and 70,000
    tokens, 10,000 of each from the provider's cache, in 2, 3 and 5 seconds), one that failed, and a compaction."""
    thread: ThreadRecord = await runtime.store.create_thread("Read the report", "test")
    run: RunRecord = await runtime.store.create_run(
        thread.id,
        "Read the report",
        "task",
        SCRIPTED_MODEL,
        None,
        "test",
        "medium",
        "standard",
        None,
        "yolo",
        False,
        None,
        None,
        None,
    )
    for step, (tokens, seconds) in enumerate(((20_000, 2.0), (40_000, 3.0), (70_000, 5.0)), 1):
        call: JsonObject = {
            "purpose": "step",
            "model": SCRIPTED_MODEL,
            "seconds": seconds,
            "input_tokens": tokens,
            "output_tokens": 300,
            "cached_tokens": 10_000,
            "error": None,
        }
        await runtime.store.add_event(run.id, CALL_EVENT, call)
        await runtime.store.add_event(
            run.id,
            "assistant",
            {"step": step, "text": "", "tool_calls": [], "input_tokens": tokens, "output_tokens": 300},
        )
    failed: JsonObject = {
        "purpose": "step",
        "model": SCRIPTED_MODEL,
        "seconds": 1.0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cached_tokens": 0,
        "error": "ProviderTransportError",
    }
    await runtime.store.add_event(run.id, CALL_EVENT, failed)
    await runtime.store.add_event(run.id, "compacted", {"compaction": 1})
    await runtime.store.end_run(run.id, "done", "Done.", {"status": "done", "result": "Done.", "needs": ""})


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def runtime(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Runtime]:
    """A runtime in a throw-away home whose providers are the stand-ins, its history seeded."""
    home: Path = tmp_path_factory.mktemp("home")
    (home / "config.toml").write_text(f"[browser]\nheadless = true\n\n{_MODELS_TABLE}", encoding="utf-8")
    patch: pytest.MonkeyPatch = pytest.MonkeyPatch()
    patch.setenv("DOT_HOME", str(home))
    patch.setenv("DOT_CHROME_DIR", str(tmp_path_factory.mktemp("chrome")))
    built: Runtime = await build_runtime(load_config(home / "config.toml"))
    for provider, models in _LISTS.items():
        await built.router.register(provider, Listed(provider, models))
    await _seed_history(built)
    try:
        yield built
    finally:
        await close_runtime(built)
        patch.undo()


@pytest_asyncio.fixture(loop_scope="module")
async def served(runtime: Runtime) -> AsyncIterator[str]:
    """The address of a real server over the runtime."""
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
            yield f"http://127.0.0.1:{port}"
        finally:
            server.should_exit = True
            stopping.set()
            await serving
    finally:
        await manager.shutdown()


async def _file(path: Path) -> str:
    return await asyncio.to_thread(path.read_text, encoding="utf-8")


async def _run_scaffold(http: httpx.AsyncClient, runtime: Runtime, model: str) -> JsonObject:
    """The scaffold event of a chat's run of ``model`` started through the API, once the run has ended."""
    started: httpx.Response = await http.post("/api/runs", json={"goal": "Say hi.", "mode": "chat", "model": model})
    assert started.status_code == 201, started.text
    run_id: str = started.json()["id"]
    async with asyncio.timeout(30):
        while True:
            run: RunRecord = await runtime.store.get_run(run_id)
            if run.status not in WORKING_STATUSES:
                break
            await asyncio.sleep(0.05)
    scaffold: EventRecord | None = await runtime.store.last_event(run_id, "scaffold")
    assert scaffold is not None
    return scaffold.data


async def _listed(http: httpx.AsyncClient, ref: str) -> JsonObject:
    """The model ``ref`` as ``GET /api/models`` lists it."""
    response: httpx.Response = await http.get("/api/models")
    assert response.status_code == 200, response.text
    return next(model for provider in response.json() for model in provider["models"] if model["ref"] == ref)


async def test_the_api_sets_a_models_context_size_in_the_file_and_for_its_next_run(
    runtime: Runtime, served: str
) -> None:
    path: Path = config_path()
    assert path == Path(os.environ["DOT_HOME"]) / "config.toml"
    async with httpx.AsyncClient(base_url=served, headers={"Authorization": f"Bearer {TOKEN}"}) as http:
        listed: JsonObject = await _listed(http, SCRIPTED_MODEL)
        assert (listed["context_length"], listed["window_source"]) == (1_000_000, "provider")
        assert listed["context"] == {
            "tokens": 96_000,
            "chosen": None,
            "default": 96_000,
            "configured": 96_000,
            "floor": 48_000,
            "ceiling": 750_000,
            "sizes": [int(size) for size in _SIZES[1:]],
            "summarise_at": 81_600,
        }
        # Below the floor, past three quarters of the window, and any size of a window that holds none: refused.
        for model, tokens in ((SCRIPTED_MODEL, 47_999), (SCRIPTED_MODEL, 750_001), (_TINY, 48_000)):
            refused: httpx.Response = await http.put("/api/models/context", json={"model": model, "tokens": tokens})
            assert refused.status_code == 422, refused.text
        assert (await _file(path)).endswith(_MODELS_TABLE)
        assert runtime.context_sizes.of(SCRIPTED_MODEL) is None

        chosen: httpx.Response = await http.put(
            "/api/models/context", json={"model": SCRIPTED_MODEL, "tokens": 192_000}
        )
        assert chosen.status_code == 200, chosen.text
        assert chosen.json()["context"]["tokens"] == chosen.json()["context"]["chosen"] == 192_000
        assert chosen.json()["context"]["summarise_at"] == 163_200
        # The next run gets it, with no restart; the file keeps it, and the rest of it as it was.
        assert runtime.context_sizes.of(SCRIPTED_MODEL) == 192_000
        written: str = await _file(path)
        assert written.endswith(f'{_MODELS_TABLE}\n[models.context]\n"{SCRIPTED_MODEL}" = 192000\n'), written
        assert load_config(path).models.context == {SCRIPTED_MODEL: 192_000}
        assert (await _listed(http, SCRIPTED_MODEL))["context"]["chosen"] == 192_000
        # A window Dot asks Ollama for: three quarters of it is Dot's default already, so the floor is the one other size.
        assert (await _listed(http, _ASKED))["context"]["sizes"] == [48_000]

        back: httpx.Response = await http.put("/api/models/context", json={"model": SCRIPTED_MODEL, "tokens": None})
        assert back.status_code == 200, back.text
        assert back.json()["context"]["chosen"] is None
        assert runtime.context_sizes.of(SCRIPTED_MODEL) is None
        assert (await _file(path)).endswith(f"{_MODELS_TABLE}\n[models.context]\n")
        assert load_config(path).models.context == {}

        # What the runs of the model measured: the steps that answered, the compactions, the seconds by input.
        evidence: httpx.Response = await http.get("/api/models/context", params={"model": SCRIPTED_MODEL})
        assert evidence.status_code == 200, evidence.text
        assert evidence.json() == {
            "model": SCRIPTED_MODEL,
            "days": 30,
            "runs": 1,
            "steps": 3,
            "input_median": 40_000,
            "input_p95": 70_000,
            "input_max": 70_000,
            "cached_share": 30_000 / 130_000,
            "first_step_median": 20_000,
            "compactions": 1,
            "compacted_runs": 1,
            "buckets": [
                {"above": 0, "up_to": 32_000, "steps": 1, "seconds_median": 2.0},
                {"above": 32_000, "up_to": 64_000, "steps": 1, "seconds_median": 3.0},
                {"above": 64_000, "up_to": 96_000, "steps": 1, "seconds_median": 5.0},
            ],
        }

        # A run started after a choice has that size, and one started after it was taken out has Dot's default: a
        # model with no steps in the history, so that the numbers above stay the scripted model's.
        opus: httpx.Response = await http.put("/api/models/context", json={"model": _OPUS, "tokens": 128_000})
        assert opus.status_code == 200, opus.text
        sized: JsonObject = await _run_scaffold(http, runtime, _OPUS)
        assert (sized["chosen"], sized["context_tokens"], sized["summarise_at"]) == (128_000, 128_000, 108_800)
        unsized: httpx.Response = await http.put("/api/models/context", json={"model": _OPUS, "tokens": None})
        assert unsized.status_code == 200, unsized.text
        default: JsonObject = await _run_scaffold(http, runtime, _OPUS)
        assert (default["chosen"], default["context_tokens"], default["summarise_at"]) == (None, 96_000, 81_600)


async def test_settings_choose_a_models_context_size_and_say_why_with_its_runs_numbers(
    runtime: Runtime, served: str, tmp_path: Path
) -> None:
    path: Path = config_path()
    host, port = "127.0.0.1", int(served.rpartition(":")[2])
    async with async_playwright() as playwright:
        browser: Browser = await playwright.chromium.launch(channel=runtime.config.browser.channel, headless=True)
        try:
            for index, (width, scheme) in enumerate(_SHOTS):
                page: Page = await browser.new_page(
                    viewport={"width": width, "height": 800}, color_scheme=scheme, reduced_motion="reduce"
                )
                await page.goto(ui_url(host, port, TOKEN))
                await page.goto(f"{served}/#/settings/models")
                size: Locator = page.get_by_role("combobox", name=f"Context size of {SCRIPTED_MODEL}")
                await expect(size.locator("option")).to_have_text(_SIZE_TEXTS)
                assert await size.locator("option").evaluate_all("(options) => options.map((o) => o.value)") == _SIZES
                # The window beside it; a model whose window holds no other size shows its budget as text.
                cell: Locator = page.locator("td.context-cell", has=size)
                await expect(cell).to_contain_text("of 1.0M")
                asked: Locator = page.get_by_role("combobox", name=f"Context size of {_ASKED}")
                await expect(asked.locator("option")).to_have_text(["49k (default)", "48k"])
                # The window Dot asks Ollama for, the model's own beside it (item 221).
                await expect(page.locator("td.context-cell", has=asked)).to_contain_text("of 66k (own 262k)")
                tiny: Locator = page.locator(
                    "td.context-cell", has=page.get_by_role("button", name=f"Why: the context size of {_TINY}")
                )
                await expect(tiny.get_by_role("combobox")).to_be_hidden()
                await expect(tiny).to_contain_text("25k")
                if index == 0:
                    await expect(size).to_have_value("")
                    await size.select_option("256000")
                    await expect(
                        page.get_by_text("scripted gets a context size of 256,000 tokens from its next run.")
                    ).to_be_visible()
                    await expect(size).to_have_value("256000")
                    assert runtime.context_sizes.of(SCRIPTED_MODEL) == 256_000
                    assert load_config(path).models.context == {SCRIPTED_MODEL: 256_000}
                else:
                    await expect(size).to_have_value("256000")
                why: Locator = page.get_by_role("button", name=f"Why: the context size of {SCRIPTED_MODEL}")
                await expect(why).to_have_attribute("aria-expanded", "false")
                await why.click()
                await expect(why).to_have_attribute("aria-expanded", "true")
                row: Locator = page.locator("tr.context-why:not([hidden])")
                await expect(row).to_have_count(1)
                body: Locator = row.locator(".context-why-body")
                await expect(body.locator("h4")).to_have_text(
                    ["Context size of scripted", "This model's runs, last 30 days"]
                )
                await expect(body).to_contain_text(
                    "Once a step sends more than 217,600 tokens, Dot summarizes the run's older part, so the run stays "
                    "within its 256,000."
                )
                # Nothing left out is written as text.
                await expect(body).not_to_contain_text("null")
                await expect(body.locator(".context-facts dd")).to_have_text(
                    [
                        "1,000,000 tokens. Ollama gives it for the model.",
                        "750,000 tokens, three quarters of the window: the rest is room for the reply and for the error "
                        "of Dot's count of the tokens.",
                        "96,000 tokens, for every model without a size of its own. Dot comes with 96,000: in a run "
                        "of 41 steps it measured, 48,000 summarized twice and 96,000 not once, while each step sent a "
                        "quarter more. In the config file: [agent] context_tokens.",
                        "48,000 tokens: a run's first step alone sends its instructions, its tools and the request.",
                    ]
                )
                evidence: Locator = body.locator(".context-evidence")
                await expect(evidence.locator("p")).to_have_text(
                    [
                        "3 steps in 1 run. A step sent 40,000 tokens at the median, 70,000 or fewer in 95% of steps "
                        "and 70,000 at most. 23% of it the provider served from its cache. A run's first step sent "
                        "20,000 at the median.",
                        "1 summary in 1 run.",
                    ]
                )
                await expect(evidence.locator("tbody td")).to_have_text(
                    ["up to 32k", "1", "2.0 s", "32k to 64k", "1", "3.0 s", "64k to 96k", "1", "5.0 s"]
                )
                # The row's text stays inside the window and short of the 40 pixels its right edge fades over while
                # more of the table lies there, also once the table is scrolled sideways on a phone, to the menu at
                # its right end.
                scroller: Locator = page.locator(".table-scroll", has=size)
                for scrolled in ("0", "node.scrollWidth"):
                    await scroller.evaluate(f"(node) => {{ node.scrollLeft = {scrolled}; }}")
                    shown = await scroller.bounding_box()
                    box = await body.bounding_box()
                    assert shown is not None and box is not None
                    assert shown["x"] <= box["x"] and box["x"] + box["width"] <= shown["x"] + shown["width"] - 40, (
                        shown,
                        box,
                    )
                await expect(size).to_be_in_viewport()
                await size.scroll_into_view_if_needed()
                await page.screenshot(path=str(tmp_path / f"menu-{width}-{scheme}.png"), animations="disabled")
                await body.scroll_into_view_if_needed()
                await page.mouse.move(0, 0)
                await page.screenshot(path=str(tmp_path / f"context-{width}-{scheme}.png"), animations="disabled")
                await why.click()
                await expect(row).to_have_count(0)
                await page.close()
            # Back to Dot's default: the key leaves the file and the next run has [agent] context_tokens.
            page = await browser.new_page(viewport={"width": 1280, "height": 800})
            await page.goto(ui_url(host, port, TOKEN))
            await page.goto(f"{served}/#/settings/models")
            size = page.get_by_role("combobox", name=f"Context size of {SCRIPTED_MODEL}")
            await size.select_option("")
            await expect(
                page.get_by_text("scripted gets Dot's default context size from its next run.")
            ).to_be_visible()
            assert runtime.context_sizes.of(SCRIPTED_MODEL) is None
            assert load_config(path).models.context == {}
            await page.close()
        finally:
            await browser.close()
