# Contributing to Dot

Thank you for helping. Dot works with your rights on your Mac, so a change is judged by what it lets a run do as much as
by how it reads.

## Before you start

- For anything larger than a fix, open an issue first and agree there on what the change should do.
- Report a security problem privately, as [SECURITY.md](SECURITY.md) says, never in an issue or a pull request.
- Everyone here follows the [code of conduct](CODE_OF_CONDUCT.md).

## Set up

Dot runs on macOS only, as it depends on PyObjC. With Python 3.13 and [uv](https://docs.astral.sh/uv/):

```bash
git clone https://github.com/senoldogann/Dot.git
cd Dot
uv sync
npm ci
```

`npm ci` installs the checks of the web interface (Biome and TypeScript, into `node_modules/`) and needs Node 22 or
later; nothing of Dot needs Node to run.

## The checks every change passes

The gate (`.github/workflows/gate.yml`) runs these on every pull request that changes more than prose. Run them once
before you push; while you work, the tests of what you changed are enough. `-n auto` (pytest-xdist) spreads the tests
over your Mac's cores:

```bash
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy src
uv run pytest tests/unit -q -n auto
uv run dotx eval check
uv run pytest tests/smoke -q -n auto -m "not live and not browser and not internet and not desktop and not hosted"
npm run check
```

`npm run check` lints the scripts of the web interface with Biome (`biome.json`), checks their types with `tsc` over
their JSDoc (`tsconfig.json`; the service worker apart, in `tsconfig.sw.json`) and runs the unit tests of their pure
modules (`tests/js`, `node --test`). `npm run fix` applies the fixes Biome can make. [Development](docs/development.md)
says how strict the type check is and what comes next.

The gate's own job leaves out the tests that need a browser or a service; its `browser` job runs the core of the browser
tests (item 486), and `.github/workflows/browser.yml` all of them every night. Run those that touch your change on your
Mac: `-m "not live and not internet and not hosted and not desktop"` adds the browser tests, which drive Google Chrome
headless against a local server. A few drive WebKit, the engine of Safari on the phone, which Playwright installs with
`uv run playwright install webkit`. [Development](docs/development.md) says what each test marker needs.

A change under `.github/` is audited by zizmor too (item 487); the same check on your Mac, with `GH_TOKEN` set for the
audits that ask GitHub (`--offline` without it):

```bash
uvx zizmor@1.31.1 --min-severity low .
```

## Tests

- A test calls no model and reaches nothing outside this Mac. A scripted model plays the model, and a stand-in service on
  `127.0.0.1` or a local page plays the rest. The tests marked `live`, `internet` and `hosted` are the exceptions, and
  they stay on your Mac.
- Point `DOT_HOME`, `DOT_LOG_DIR` and `DOT_CHROME_DIR` at throwaway folders when you run the tests, so that none of them
  touches your own Dot.
- Prefer the real thing to a mock: the real server, the real store, a real browser against a local page.
- Add the test a change needs, and check that it fails without the change.

## Code

- Python is typed throughout: `mypy` runs strict, and data from outside (a file, an answer of a service) is validated
  into a model where it comes in.
- An error says what failed, with what was asked and what came back, and is raised where it happens. A fallback that
  hides a failure is not added.
- A handler that catches `Exception` raises it again, logs it with its traceback, or says where it goes in a
  `# noqa: BLE001 - <reason>` comment: `ruff check` refuses one that does none of these.
- A comment or a docstring says what the code does now, not how it came to be.
- A line of Python is at most 120 characters, and `ruff check` holds it to that; new Markdown prose is wrapped the same
  way.
- The web UI is plain JavaScript modules and CSS under `src/dotagent/server/static/`, with no build step.

## Words on the screen

- A screen is in words that someone who knows no software reads at once: "Dot" and not "daemon", "task" and not "run",
  "key" and not "token". The words of a program (MCP, API, config, tool, preset...) stay out of plain sight: they stand
  under a closed **Details** or **Advanced** fold, with a setting's key, a command or a code, or in the tooltip of an
  element marked `data-expert` (a schedule's cron beside its rhythm in words).
- A button says what it does, an empty state what will be there and how to get it, an error what happened and what to
  do next; a page's first line says what it is for; one name stands for a thing on every screen, toasts included.
- `tests/smoke/test_plain_words.py` opens each screen, and each menu and dialog of it, on a daemon with something to
  show, and fails on the words of its list: add a new screen to it, and a word to its list when one comes back.

## Pull requests

- A pull request carries one change or a batch of related ones, each its own commit with its own roadmap and changes
  entry. Say what each does, why, and how you checked it; the template asks for each.
- `uv run python scripts/next_item.py [count]` gives the numbers new items take (main, open pull requests and pushed
  branches hold numbers); `uv run python scripts/pr_body.py <item>... [--title]` makes the pull request's title
  and body from the roadmap entries of its items.
- A change in what Dot does changes the page under [docs/](docs/) that describes it, in the same pull request.
- Its changelog entry is a file of its own, `changes/<item>.md`, and its roadmap item `roadmap/<item>.md`
  ([changes/README.md](changes/README.md), [roadmap/README.md](roadmap/README.md)): pull requests made side by side then
  add files instead of conflicting at the top of CHANGELOG.md and the end of ROADMAP.md.
- Write the commit message as a sentence that says what the change does.
