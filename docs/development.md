# Development and measuring the agent

## Development

```bash
uv sync
.venv/bin/ruff format src tests && .venv/bin/ruff check src tests && .venv/bin/mypy
.venv/bin/pytest
npm ci && npm run check
```

The web interface has checks of its own, on Node 22 or later (a development requirement only; nothing of Dot needs
Node to run). `npm run check` runs Biome's recommended lint rules over `src/dotagent/server/static/**/*.js`,
`desktop/ui` and `tests/js` (`biome.json`; two rules of style alone are off), `tsc` over the scripts' JSDoc with
`checkJs` (`tsconfig.json`, and `tsconfig.sw.json` for the service worker, whose globals are a worker's; the types the
DOM library lacks are in `types/interface.d.ts`), and the unit tests of the pure modules (the Markdown reader, the words
of a flow, the formatting helpers, the reasoning levels) under `tests/js` with `node --test`. The type check starts
loose: `strict` is off, so a parameter without a type is no error, and what it catches is a property an element does
not have, a call with the wrong arguments, a union read without a check. `noImplicitAny` is the next step, taken once
the modules carry their JSDoc. Biome's formatter is configured but off, as the scripts were not written to its line
width; it is switched on in one commit of its own. `tests/unit/test_static_ui.py` reads the scripts the way the browser
would: every import names an export of a script that is there, no export is left that nothing imports (the tests count
as importers; a script imported whole counts for the names its importer reaches through the name it gave it, and a page
loaded on demand for its `mount`: item 559), the page has no inline code, its `modulepreload` links name every module
`main.js` reaches at the start, and the pages loaded on demand (`import()` in main.js's route table: everything but the
chat and the home) exist.

The tests prefer real calls to mocks: the smoke tests start the real server, a headless Chrome against a local fixture
server, a real MCP server process and real Ollama models, and the unit tests cover pure functions. Tests that need a
service are marked:

| Marker     | Needs                                                                                                                |
| ---------- | -------------------------------------------------------------------------------------------------------------------- |
| `live`     | a running Ollama server with a signed-in cloud model; the voice test (item 626): macOS 26 and Apple's developer tools |
| `browser`  | Chrome; it runs headless against a local fixture server                                                              |
| `internet` | the public internet; read-only requests: a few, two or three to each connector, a download of each gallery plugin    |
| `desktop`  | a logged-in screen and `DOT_DESKTOP_TESTS=1`; it moves the real pointer and keyboard against a local AppKit window |
| `hosted`   | your own `OPENAI_API_KEY`, `OPENCODE_API_KEY`, `ANTHROPIC_API_KEY` and `OLLAMA_API_KEY`; a few tiny requests, skipped per missing key |

`pytest -m "not live and not internet"` skips the first and third kind, `pytest -m "not hosted"` the requests that use
your keys. A few smoke tests that use the local Ollama server's embedding model skip themselves without it; that model
runs on this Mac. A test that asks the server for a cloud model (a name that ends in `:cloud`, which Ollama answers from
its own servers) is `live`, as every test that calls a model is (`tests/smoke/ollama_live.py`, item 632): before, such
tests were only skipped where the server was not there, so the offline selection of the gate called the model on a Mac
that had it. A bare `pytest` collects every test, as each test module is imported by its path (`tests/unit` and
`tests/smoke` have modules of the same name), and a mark that is not in the table fails, so a mistyped one cannot pick a
test that needs a service (item 213).

No test opens a microphone (item 626). Chrome's own fake microphone (`--use-fake-device-for-media-stream`) still makes
macOS ask for the microphone for the app that runs the tests, a permission dialog on the screen, and `getUserMedia`
waits for its answer. So the browser tests of the composer's recording (`tests/smoke/test_voice_recording.py`) replace
`getUserMedia` with a function that gives a stream made by Web Audio, a tone the test turns on and off; the page's
capture, its audio worklet, its downsampling, its WAV file and its request run as they do for a real microphone, in
Chrome and in WebKit, Safari's engine. The program that writes a recording down is a stand-in of the test's own
(`tests/smoke/voice_stand_ins.py`): `DOT_SWIFTC` names a compiler that copies it, and every smoke test starts with
`DOT_SWIFTC` naming one that does not exist, so that no test builds Swift or asks the owner's developer tools. The one
test that builds the real program, `tests/smoke/test_voice_live.py` (`-m live`, which may download a language's model
from Apple the first time), records with `say`.

No test shows System Settings (item 636), nor reads the Dot.app on the owner's Mac. The route that shows it is given an
opener of the test's own (`tests/unit/test_voice_desktop_app.py`), the program `DOT_OPEN` names is one that does not
exist for every test, and the browser tests answer the page's ask that the Mac show it themselves (`page.route`), so
that the daemon never sees it. The apps the reader looks at are bundles written in the test's folder, and the `codesign`
of the unit tests is a stand-in that says what the real one says; the one test that runs the real `codesign`
(`tests/smoke/test_desktop_app_signature.py`) signs throwaway bundles ad hoc, with no identity and no keychain.

A test never finds your keys unless it is marked `hosted`, `internet` or `live` (item 558, `tests/conftest.py`): in a
run where none of those is selected the variables of the providers' keys are out of the environment and the login
Keychain is never asked, from the first fixture on; in a run that holds some, each of the others goes without them
while it runs. A test that needs a key of its own sets one under a name it makes up, and a test that starts a child
process gives it a key variable no source has. The tests that do use your keys look for them as they run (a module
that asked as it was read made every run, the offline ones too, wait for the Keychain, and stop at its prompt).

The tokens of the accounts signed in (item 422) and the smoke tests keep theirs out of your login keychain the same
way: `tests/smoke/conftest.py` names a keychain file that is not there (`DOT_KEYCHAIN`), and the tests of the sign-ins
make one of their own. A file that is not there is refused before `security` runs, and so is a path that is not
whole, which `security` would look for under `~/Library/Keychains` (item 628): `security` does not fail on a missing
keychain file, it reads and writes the login keychain in its place, and a test's item was found there.

Dot's own vault (item 621) keeps its secrets in the login Keychain, and no test asks it: the tests of the vault and of
its tools stand in a dictionary for it (`MemorySecretStore` in `tests/smoke/vault_stand_ins.py`), and the one test of the
real `security` (`tests/smoke/test_vault_keychain.py`) uses the `keychain_file` fixture, a keychain of its own. A
keychain file that is not there is no safe stand-in, for `security` does not fail on it: it keeps, reads and removes the
item in the login keychain in its place. So `LoginKeychain` refuses a `DOT_KEYCHAIN` that names no file before it starts
a process (the smoke tests point `DOT_KEYCHAIN` at such a path), and a test that needs `security` makes a file first.

The web-research server that comes with Dot (item 583) is out of every test that does not ask for it: its package is
installed where the tests run, so each of the runtimes a test builds would start a Python process of its own, and its
tools would be among those a test counts. `tests/conftest.py` looks for the package under a name nothing has, for the
whole run; a test of the server asks for the real name back (the `web_research_comes` fixture, or a module fixture that
sets `builtin.WEB_RESEARCH_PACKAGE`, as `tests/smoke/test_builtin_mcp.py` does). A `dotx` or a daemon that a test starts
as a child process finds the package: its config says `[builtin_mcp] web_research = false` where the test counts
servers.

The folder Dot copies the pictures it sends over iMessage into, `~/Pictures/Dot` (item 617), is yours and not Dot's
home: a channel that loads lists it and removes the old copies in it, so no test reaches it. `tests/conftest.py` points
`DOT_STAGING_DIR` at a folder of the run's own for the whole run (a `Channels` that was given none and a daemon that a
test starts as a child process use it), and fails a test that builds a `Staging` anywhere but in a pytest folder, before
it touches anything, as `stop_file()` in `tests/smoke/channel_stand_ins.py` refuses a home that is not a test's own. A
test that builds `Channels` gives it `own_places()`: a home and a staging folder inside the Dot home of its own.

The calls of that server's tools are judged by the rules of Dot's own `web_search`, `web_fetch` and `web_crawl`, which
are one function each in `tools/web.py` (`search_flags`, `checked_address`); `tools/mcp.py` calls the same functions, so
a rule added to those tools goes in them and binds the server's tools too (`tests/smoke/test_builtin_mcp.py` changes a
rule and watches both). The same file lists the string arguments of the server's tools as Dot sees them, and fails for
one that Dot neither judges (`mcp/builtin.py`) nor lists as staying inside the server, with what the server does with it
(`STAYS_IN_THE_SERVER`): raising `dotagent-web-research` to a version that takes a new string is a failing test, and
not a call that Dot does not judge.

The smoke tests of the eval runner (`tests/smoke/test_evals_runner.py`) need no service at all: a scripted model
plays the real loop, tools, policy and store. The two that play the form task in a headless Chrome (it and the whole
catalog's check) are marked `browser`, so that `-m "not browser"` starts none; the gate plays the catalog with
`dotx eval check` as a step of its own.

A browser test that waits for something only an async function of the page can tell (the service worker's caches)
polls it with `page.evaluate`: Playwright's `wait_for_function` takes the promise such a function returns for true at
once, and waits for nothing (item 359).

The gate runs on every pull request and every push to `main` that changes more than prose, and, called by the release,
on the tagged commit (`.github/workflows/gate.yml`, on macOS because the package depends on PyObjC; a change to the
Markdown at the root, `docs/` or the issue and pull request templates alone skips it): the format check, the lint, mypy,
the checks of the interface (`npm run check`), the unit tests, `dotx eval check` and every smoke test that needs no
service, chosen by its marks (`-m "not live and not browser and not internet and not desktop and not hosted"`). The
tests run spread over the cores with `-n auto` (pytest-xdist). Beside it, its `browser` job runs the core of the browser
tests (item 486) in two parts, the pages and the phone, each on a runner of its own and one worker (a runner has three
cores and a worker drives a Chrome and a daemon; at two or three workers pages ran out of their time): the files whose
names start `test_browser_` and `test_phone_` (a new one joins by its name), the secret fields, the page guards and the
face, in Google Chrome, Playwright's headless shell and WebKit (the phone's engine); a red one there hides nothing of
the gate's. `.github/workflows/browser.yml` runs every browser test every night and when asked (`gh workflow run
browser.yml --ref <branch>`), on one worker, for hours; a red night is GitHub's to show, and blocks nothing. The phone's
test of a network that wakes after the page comes back measures seconds of grace against the runner's clock, which a
slow runner does not keep, so the core leaves it to the night and to your Mac. A browser test that leans on another
test's state fails there, as the browser tests run without the others: each makes what it needs; and one that saves a
note turns embeddings off (`[memory] embed_enabled = false`), as no Ollama server runs there. When something under
`desktop/` changes, `.github/workflows/desktop.yml` runs `cargo fmt --check`, clippy and `cargo test` for the desktop
shell. Each action the workflows use is pinned by the commit its tag named, so a tag moved later cannot change what
runs. The tests marked live, internet, desktop or hosted stay on your Mac. Once a week, and when asked,
`.github/workflows/audit.yml` checks the dependencies as locked against the public advisories (pip-audit for `uv.lock`,
cargo audit for the desktop shell's `Cargo.lock`) and fails on a known vulnerability (item 217). The advisories
`desktop/src-tauri/.cargo/audit.toml` ignores are of crates that are not in the Mac build, the only one Dot makes
(item 766: `zip`, which `tauri-plugin-updater` pulls for Windows alone), and `desktop/src-tauri/tests/mac_build.rs`
fails the desktop checks the day one of them reaches it. It exports the whole of
`uv.lock`, `dotagent-web-research` with it, since PyPI has it and the lock holds its hashes (item 623; while uv got it
from its repository, item 583, it was left out, as `pip-audit --disable-pip` refuses a requirement with no hash).
When something under
`.github/` changes, every week and when asked, `.github/workflows/zizmor.yml` audits the workflows and Dependabot's file
with [zizmor](https://docs.zizmor.sh/audits/) (item 487): an event's text run as a script, a checkout that keeps its
token, an action pinned by a commit its repository does not have, a known vulnerable action, a cache read by a release,
Dependabot taking a release the day it is out (it waits a week, `cooldown` in `dependabot.yml`). Any finding fails the
job; one accepted is in `.github/zizmor.yml` with its reason. zizmor is pinned by its version in the workflow, as
pip-audit is; raise it by hand when a release brings an audit worth having (`uvx zizmor@<version> --min-severity low .`
first, on your Mac).

The work on the agent's autonomy (measuring it, noticing a stuck run, adapting to the model, resuming, learning) is
planned and tracked in [ROADMAP.md](../ROADMAP.md) and, from item 459 on, in [roadmap/](../roadmap/); read them before
continuing that work.

```
src/dotagent/
  cli*.py     the command line: the parser and the command models (light), one module per command family loaded by name
  agent/      run loop, run manager, approvals, context trimming, vision relay
  llm/        providers (Ollama, OpenAI, OpenCode Go, Anthropic, OpenRouter) and their wire codecs
  policy/     action descriptions, shell analysis, the decision engine
  tools/      the tools the model can call
  browser/    Playwright session, snapshots, safety
  desktop/    accessibility tree, input, screen capture, permissions
  web/        search and fetch
  memory/     notes, ranking, embeddings
  harness/    the versions of what a run's prompt takes from the harness (docs/harness.md)
  evals/      eval tasks, the runner that plays them, the scripted model, scoring
  scheduler/  schedules, timing, the runner
  mcp/        MCP client hub
  skills/     skill discovery, frontmatter and the catalog
  plugins/    plugin discovery, their skills and MCP servers
  server/     REST API, event streams, daemon, the static web UI
  store/      SQLite persistence
  core/       errors, logging, retries, paths, shared types
```

## Pinned versions of what Dot starts

The packages Dot starts on this Mac for a connector (`npx`, `uvx`: `mcp/catalogue.py`) are pinned to one exact version
each (item 695), so that a release of one of them reaches Dot only when you raise it. Before a release, or when a
connector's own news asks for it, run `uv run python scripts/catalogue_versions.py`: it asks npm's registry and PyPI for
the newest version of each and lists it beside the pinned one (the network, and no test runs it). Read what a newer
release changed, try the server (add it on Customize › Connectors), then change the number in the catalogue; the unit
test `test_each_server_started_on_this_mac_runs_the_one_version_the_catalogue_pins` fails on a package left with no
exact version. Dot's own dependencies are pinned by `uv.lock`.

## Releasing

A release is one version, the same in `pyproject.toml`, `src/dotagent/__init__.py`, `desktop/package.json`,
`desktop/src-tauri/Cargo.toml` and `desktop/src-tauri/tauri.conf.json` (`tests/unit/test_version.py` keeps them alike),
with its section `## <version> (<date>)` in `CHANGELOG.md`, which `uv run python scripts/fold_changes.py <version>
<date>` writes from the entries under Unreleased and the files of `changes/` (and removes those), in the commit that
raises the version. It is made from one commit of `main`, in two parts: the
desktop app, built on a Mac, and the Python package, which the release's tag publishes on PyPI. A version is released
once: the next release raises it in the five files first. The desktop app installs the Python package of its own
version from PyPI (item 575), so the package comes first: the tag, then PyPI listing the version, then the release that
publishes the app (the order is below, and `desktop/release.sh` ends with it).

### The package, and what it depends on that PyPI must have first

Dot's package depends on `dotagent-web-research` (item 583), the MCP server that comes with Dot, at one version
(`==1.4.9` in `pyproject.toml`). It is published on PyPI from its own repository (`senoldogann/web-research-stack`): a
tag `v<version>` there publishes it through trusted publishing under the owner's account, as `release.yml` does Dot's. A
new version of it goes in Dot by raising that pin and running `uv lock`, in a commit of Dot's own, once PyPI lists the
version: the wheel and the sdist ask PyPI for the pinned version (`Requires-Dist` in their metadata, which `unzip -p
dist/*.whl '*/METADATA'` shows), so a Dot published before PyPI has it installs nowhere until it does, and a version
once published is never replaced. While a version is not on PyPI yet, a `[tool.uv.sources]` table may name its
repository at a fixed commit for `uv sync` and `uv.lock`, as the work of 0.2.0 did until 1.2.0 was uploaded (item 602).
A uv source is not carried into the built wheel, and `pip-audit` refuses a requirement that has no hash, as one from a
repository has none: the table goes, with the `--no-emit-package` it needs in the `uv export` of
`.github/workflows/audit.yml`, in the commit that raises Dot's version.

### The desktop app

`desktop/release.sh` (item 432) builds it into `desktop/dist/<version>/`, which git ignores:

- `Dot_<version>_universal.dmg`, the disk image to install from: the app for Apple silicon and Intel and a link to
  Applications, the image and the app each signed with the Developer ID (the app with the hardened runtime), notarized
  by Apple and stapled, so that Gatekeeper opens them on a Mac that is offline;
- `Dot_<version>_universal.app.tar.gz` and its `.sig`, the archive the shell's updater installs: the stapled app,
  signed with the updater's key, the signature naming the version;
- `latest.json`, what the updater reads from the latest release: the version, and the archive's address and signature
  for both kinds of Mac;
- `notes.md`, the version's section of the changelog, the release's notes.

The app carries three programs in its Resources (`Contents/Resources/tools`), which the installer uses on a Mac that
has no Dot and the service's `PATH` names (item 575). `desktop/fetch-tools.sh` makes them before the app is built, and
`release.sh` runs it with `--sign`:

- `uv` and `rg`, each one universal program joined from the archives of both kinds of Mac that `desktop/tools.lock`
  pins (version, address, the SHA-256 of the archive and the SHA-256 of the program in it). The script reads each
  archive once, so that the bytes hashed are the bytes unpacked, and stops unless both hashes are the pinned ones (an
  archive swapped after its check gives another program, which the program's pin catches); the two halves of the joined
  program are checked against the pins again before anything is signed;
- `dot-launcher`, the program of Dot.app, built for both from `src/dotagent/dot_app.c`, which `dotx service install
  --launcher` copies in place of the clang build.

Each is signed with the Developer ID, the hardened runtime and a secure timestamp before the app is signed, since
Apple's notary service asks it of every program inside an app, and `release.sh` checks the three in the built app.

The folder of the tools is the one door by which a program could reach every user's daemon: the app carries it, and the
service's `PATH` begins with links to two of its programs. So three locks hold on it, one behind the other (the review
of item 575). `tauri.conf.json` names the three programs one by one (`bundle.resources` has no glob), so a file that
lies in `desktop/src-tauri/resources/tools` is not bundled. `fetch-tools.sh` empties that folder of everything but its
README before it writes the three, and refuses a folder that has no such README, so that a wrong `--out` costs nobody
their files. And `release.sh` asks `fetch-tools.sh --check-bundle` of the app it built: the app's tools folder holds
exactly `dot-launcher`, `rg` and `uv`, each a program of its own that can be run, and the release stops otherwise. A
build that has not fetched the tools (a plain `npm run build`, the tests) gets three empty stand-ins from `build.rs`,
which are no programs: the app then has no installer.

`desktop/release.sh --check-tools` only fetches, unpacks and checks the archives and their programs (no signing, nothing
built: it runs without your Developer ID, and `tests/smoke/test_fetch_tools.py` runs it against the real releases at
night), which tells you whether a pinned release was moved. To raise a tool, change its version, address and both
SHA-256s for both kinds of Mac together in `tools.lock` (uv and ripgrep each publish a `.sha256` of their archives; the
program's is `shasum -a 256` of the file you unpack from the archive you checked), then run that check. The
suggested models of the page (`desktop/src-tauri/providers.json`) are the ids the providers' own lists give: look them
over at each release, since a model a provider retired fails the install with words, not silently.

The app also carries the versions its package's dependencies were built and tested at (item 694): `pyproject.toml`
declares lower bounds only, so an install with no pin would take the newest release of every dependency on the day it
runs, on every new Mac, with the daemon that holds the keys. `release.sh` writes `constraints.txt` into the app's
Resources (beside `tools`, not in it) from `uv.lock` with the `uv` the app carries (`uv export --locked --no-dev
--no-emit-project --no-hashes`: a `name==version` line for each runtime dependency, with the markers that keep it to
the Macs it is for), before the app is built and signed, and once the app is built compares its copy with a fresh export
of the lock: the release stops when `uv.lock` is not what `pyproject.toml` asks for, or the app's file is not the
lock's.
The installer gives it to `uv tool install --constraints` at the install and at the upgrade. The file has no hashes
because `uv tool install` ignores the hashes of a constraints file (checked with uv 0.13.4: a wrong hash installs all
the same, and the tool command has no `--require-hashes`), so the pin is of versions: a release that PyPI replaces under
the same version number is not caught. A build with no such file (a development build gets an empty stand-in from
`build.rs`) installs as it did before.

It stops at the first check that fails, and says which: files not committed, a commit that is not on GitHub's `main`,
versions that differ, a tag that is on GitHub already, a changelog with no section for the version, a tool that is not
the pinned one, a `uv.lock` that `pyproject.toml` has moved from, the app's signature (the Developer ID, the hardened
runtime, a secure timestamp, both architectures),
the app's reasons for the microphone and speech recognition and its microphone entitlement (item 594), a tools folder
in the app that does not hold exactly the three programs, the signature of the three in it, a `constraints.txt` in the
app that is not what `uv.lock` pins, and a verdict of Apple's
notary service other than Accepted, with the log of what it found.

Once, on the Mac that builds releases:

- Rust from rustup, first in `PATH` (Homebrew's Rust builds for this Mac only), with the targets of both kinds of Mac:
  `rustup target add aarch64-apple-darwin x86_64-apple-darwin`; and Node, as for any build of the shell.
- A Developer ID Application certificate in the login keychain, the only one there: Xcode › Settings › Accounts ›
  Manage Certificates, or developer.apple.com › Certificates.
- The notary service's credentials, saved as the profile `dot-notary`:
  `xcrun notarytool store-credentials dot-notary --apple-id <Apple ID> --team-id <team ID>`, which asks for an
  app-specific password (account.apple.com › Sign-In and Security › App-Specific Passwords).
- The updater's key pair: in `desktop/`, `npx tauri signer generate -w ~/.tauri/dot.key`; its password in the Keychain
  with `security add-generic-password -s dot-release -a TAURI_SIGNING_PRIVATE_KEY_PASSWORD -w`, which asks for it; and
  the public key, the content of `~/.tauri/dot.key.pub`, as `plugins.updater.pubkey` in
  `desktop/src-tauri/tauri.conf.json`. Keep the private key and its password: an installed shell takes only updates
  signed with it, so without them each copy would have to be installed again by hand.

Then, on a clean checkout of the commit to release:

```bash
desktop/release.sh
```

The script publishes nothing. It ends with the commands that do, with the commit and the files, in the order the app
needs. The app installs `dotagent-macos==<its version>` from PyPI, on a Mac that installs Dot from the image and on one
whose app has just updated itself, so a version of the app that is published before its package is on PyPI fails every
install and every update until it is. The order, then: the tag (which publishes the package), a wait for PyPI, and only
then the release (which publishes the app, its update and `latest.json`):

```bash
git tag v0.1.0 <commit> && git push origin v0.1.0
until curl -fsS https://pypi.org/pypi/dotagent-macos/0.1.0/json >/dev/null; do sleep 30; done
gh release create v0.1.0 --verify-tag --title "Dot 0.1.0" --notes-file desktop/dist/0.1.0/notes.md \
  desktop/dist/0.1.0/Dot_0.1.0_universal.dmg desktop/dist/0.1.0/Dot_0.1.0_universal.app.tar.gz \
  desktop/dist/0.1.0/latest.json
```

`--verify-tag` stops the release unless the tag is on GitHub already. The tag publishes the package once the gate passes
again on its commit (below); a run that fails publishes nothing, and then the release is not made: fix `main` and
release the next version. Once the release is the latest, each installed shell finds it as it starts or from Check for
Updates… (see [the desktop shell](daemon.md#the-desktop-shell)), and upgrades the package it installed to the same
version.

### Trying a release on a Mac with no Dot

The installer cannot be tried on a Mac that has Dot (it shows what it always showed there), and building and notarizing
the image is the owner's step, so a release is tried on a new macOS user account of the Mac that built it, or on
another Mac, before it is announced:

1. Make a user (System Settings › Users & Groups), sign in as it, and open `Dot_<version>_universal.dmg`; drag Dot to
   Applications and open it from there. The window shows **Set up Dot**, and the first step lists the macOS version
   and Chrome.
2. Choose a provider (a key of your own, or Ollama if it runs on that Mac), press **Install Dot**, and watch the four
   steps. Afterwards `~/Library/Application Support/Dot/runtime` holds the environment (`uv-tools`, `python`, `cache`,
   `app-tools` and `app-install.json`), `~/Library/Logs/Dot/installer.log` the steps' output (no key in it), and
   `launchctl print gui/$(id -u)/dev.dotagent.dot` the job, whose `PATH` begins with `.../runtime/app-tools`.
3. The window opens the web UI by itself. Run a goal that searches files (it needs `rg`, which Homebrew does not give
   this user).
4. Quit Dot, open it again: it shows the UI. Close it in the middle of an install, or press Try again after a failure (a
   wrong key, a model the provider does not list): the installer comes back at the step that stopped (after the models,
   at the service, with no question and no second `dotx setup`). Once the service is installed the install is complete:
   quit Dot while it waits for the daemon, open it again, and it shows the UI, or the page that says Dot is down, never
   Set up Dot.
5. Install the next release's app over it (or press Check for Updates…): the package follows, and `dotx service status`
   (`runtime/bin/dotx`) shows the daemon of the new version once no run works.

### The Python package on PyPI

Dot is published on PyPI as `dotagent-macos` (`dotagent` was taken; the package it installs is still `dotagent`, and
the command `dotx`), by `.github/workflows/release.yml` (item 348) through trusted publishing, so no token is kept
anywhere. Once, on pypi.org, with two-factor authentication on: Account settings › Publishing › Add a new pending
publisher › GitHub, with the project name `dotagent-macos`, the owner `senoldogann`, the repository `Dot`, the workflow
`release.yml` and the environment `pypi`. Then any tag `v<version>` pushed to GitHub publishes the package (a release
is made after it, `gh release create --verify-tag`, so that the app, which installs the package of its own version, is
never out before the package; see above); on its own:

```bash
git tag v0.1.0
git push origin v0.1.0
```

The workflow publishes only what the gate passed again (item 457): a first job checks that the tagged commit is a commit
of `main` and that the tag and the version agree, the whole gate then runs again on that commit (`gate.yml` is called,
whatever the change), the core of the browser tests with it (item 486), and only when both passed does the last job
build the source distribution and the wheel with `uv build` and upload them with `uv publish`; a failed or cancelled job
before it skips it, and nothing reaches PyPI. A tag to publish is about eight minutes from PyPI, and the GitHub release,
which the desktop shell's updater reads and is out at once, waits for it (above). When the run is red, fix it on `main`
and make the release again with the next version: PyPI takes a version once, and the tag that failed published nothing,
and no release was made on it. These checks
stop a mistake, not someone who holds a token that can push tags (a leaked one, or an agent working under your login):
the workflow that runs is the tagged commit's own, which such a token can write too. Two settings of the repository,
yours to set, close that: yourself as the required reviewer of the environment `pypi` (Settings › Environments › pypi),
so that every publish waits for your click, and a ruleset that lets only you create tags `v*` (Settings › Rules ›
Rulesets). Installed from PyPI, Dot runs as from a clone: `uv tool install dotagent-macos`, then `dotx setup`.

## Measuring the agent

How well Dot finishes work is measured, not guessed, in two ways.

**`dotx stats [--days N]`** counts the runs that really happened (the last 30 days by default), per model: how they
ended (finished, stopped by you, stopped by the progress watchdog, stopped at its limit, refused by the goal check, cut
off when the daemon died or shut down, internal error, still active), steps, input and output tokens, tool calls and how
many of them failed, calls whose arguments were not valid JSON, stalls and compactions. A **Time** table follows, per
model of the runs as above (a side call that another model answered counts under the run's model): the seconds the model
calls of its runs took, the median and the 90th percentile of one step's model call (of the steps that got a reply), the
seconds of the compactions and of all side calls (every call but a step: compactions, approvals the review model
answered, the goal check, the watchdog's second opinion, lessons, descriptions of images, side questions), and the
tokens the provider served from its prompt cache. A call's seconds include the retries its connector made and the waits
between them. Every model call of a run leaves a `model_call` event with its purpose, model, seconds, the seconds its
first streamed word took (`first_token_seconds`, item 404: the prompt's processing, which the provider's cache shortens;
null when nothing streamed) and tokens (and, for a call that failed or was cut off, the error), and its tokens count in
the run's totals; runs made before calls were timed have none, are left out of the table and are counted below it. A
**Tools** table lists the 15 tools whose calls took the most seconds: calls, failures, seconds in all, the median call
and the seconds spent at the gate (waiting for your approval or the review model's answer), which the seconds include.
The seconds of `ask_user` are the wait for your answer (none for a question the run left), and those of `delegate` hold
the whole delegated run, whose own tool calls are listed too. The database file is opened read-only, so it is safe to
run while the daemon works, and the numbers hold no text of your chats.

Two more numbers say how many round trips a run spends. Under each model, an **acts** line counts its `browser_act`
and `desktop_act` calls, the share that carried one step (a round trip spent on a single click or keystroke: the
batches of [Browser use](browser-and-computer-use.md#browser-use) and [Computer use](browser-and-computer-use.md#computer-use) exist to avoid it), the share that failed and
the median call; a **step model call by step number** line gives the median seconds of a step's model call for the
steps 1 to 5, 6 to 10, 11 to 20, 21 to 40 and from 41 on of their runs, which shows how much a run slows down as its
context grows. The Time table ends with the share of the input tokens the provider served from its cache. A **Sites**
table follows, per site (the host of the page for a browser act, the application for a desktop one, both recorded in
the `tool_result` event with the steps of the batch): acts, the share with one step, the share that failed, the
median call, and the finished runs that acted there with the share of them that ended done (item 308). **By day**
follows: per day, the finished runs that acted on a site and the share done, which shows whether the browser and the
desktop work gets better from day to day. `--by site` prints these two only, `--by model` everything but them. Acts and steps recorded before
these were counted (before item 22 of the roadmap) are left out of the acts lines and the sites table.

The numbers of a long run follow under each model: the **compactions** per 100 steps, their seconds and their share of
the model time (a run on a small context budget pays a summary every few steps); the **web failures** by cause, read
from the first line of each failed `web_fetch`, `web_search`, `web_crawl` or `web_extract` result (not found: HTTP 404
or a host name that does not resolve, which is an address the model guessed; throttled: HTTP 429 or an engine's bot
check; blocked: HTTP 401 or 403; in transit; other); the calls of any tool **refused for arguments that did not fit**
(a turn lost each, on top of the calls whose arguments were not valid JSON); the **delegated runs** and the seconds
the run waited for them; and the step line adds the median input tokens a step sent per band, the growth of the
context. **`dotx stats --run ID`** prints the same for one run: its minutes of wall time (to now while it works), its
tool calls' seconds and the part spent at the gate, the version of [the harness](harness.md) it took (or Dot's own
text), then the lines above and its tools. The daemon serves the same as `GET /api/runs/{id}/stats`. A **Harness**
table follows the learning lines (item 588): the finished runs of the window by the version of the harness they took,
the busiest first, with the share that ended done and the runs on Dot's own text beside them, which is how a
version's effect on its runs is read.

**What a run's first step carries** (item 735): the first step of every run records, as a `context_parts` event, what
its request holds in characters, the system prompt by the heading of each section, the tools' schemas by group (`core`,
`browser`, `mcp__<server>`...) and the conversation by role, with the whole in Dot's estimate of tokens (3.2 characters
each). `dotx stats` ends with the medians of the newest 200 runs of its window: in all, the system prompt and its five
largest sections, the tools and their five largest groups, and the conversation before the first step. A fresh
scheduled run of the owner's sent 50,000 to 61,000 tokens before any work on 2026-10-27, a fresh home's about 20,000;
`tests/smoke/test_context_budget.py` holds a fresh home's task run under a budget (21,300 in all, the system prompt
7,000, the tools 14,400), and a change that needs more raises it there, saying why.

**`dotx eval`** plays a catalog of small tasks against a model and judges each by what is left in a workspace of its
own: file edits, shell work, fixing code until its test passes, recovering from an error, multi-step work, a goal that
asks for as much as the files give, read-only research, a task that cannot be done and has to end as an honest failure,
an instruction hidden in a file that must not be followed, and a goal written in Turkish. Five long tasks follow, each
needing ten or more tool turns (a digest of twelve logs, a rename across ten modules, ten branch totals into JSON, a
ten-item checklist, a nine-stage pipeline whose fifth stage fails once): on them the steps a pass costs say how many
round trips a model spends on work that cannot be done in one. Two tasks run against a small site the attempt serves on
a loopback port (no network, no account): a research run that reads the careers pages of ten companies, one of them
linked to a dead address and one behind a redirect, and reports a table (a long task: it is where the compactions and
the not-found pages of a long run are counted); and a run in goal mode that fills in and submits a form in the headless
browser, whose end the review model checks against the goal on the confirmation page. Three tasks bring help that makes
them short, and ask whether a run takes it: a skill that states the house style a memo has to follow (the only way to
pass), a skill whose one command totals a quarter's paid orders, and a tool of a small MCP server the evals ship
(`python -m dotagent.evals.tool_server`) that counts working days; the last two can also be done the hard way, by
reading the files and working it out. Five choice tasks follow (item 659, below): what a run picks when nobody picks for
it.

| Command                                                  | What it does                                                                                                                                                                                                       |
| -------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `dotx eval list`                                         | The tasks, with their kind, mode and number of checks.                                                                                                                                                             |
| `dotx eval check`                                        | Needs no model and costs nothing. Replays the reference solution of every task through the real loop, tools and policy, and proves that a run that touches nothing and says "done" fails every task and that the reference of a task with an easy way takes it. Run it after changing a tool, the policy or the loop. |
| `dotx eval run [--model REF] [--task ID]... [--repeat N] [--no-mcp-purposes]` | Plays the tasks against a model (the configured main model if none is given) and prints the score. It spends the tokens of that model: an attempt is cut after 300 s or 600000 tokens (180 s or 250000 for a choice task), and the notice before it starts states the ceiling. `--repeat` plays every task several times, which shows the tasks a model passes only now and then. `--no-mcp-purposes` plays them with `[agent] mcp_purposes` false (below). |
| `dotx eval tools [--model REF] [--case ID]... [--repeat N]` | Asks a model the moments of tool use Dot's runs got wrong (item 736), one call each: each case's goal is started as a run of the scripted model, whose first request (the system prompt and the tools' schemas, as Dot makes them) is asked of the model with the steps before the moment, and the first call that does work is judged (the tool, its arguments, the calls it must not make). The request is captured in `yolo`, so that no case waits on an approval. It spends the tokens of that model, about 20,000 a call; its key comes from the environment and the home's `.env` alone, and the Keychain is not asked while the cases play: a key in neither is missing, and the command says so before the first case. The cases are in `evals/tool_cases.py`, each saying which failure it comes from. |
| `dotx eval report [FILE...]`                             | The score of saved runs, with a comparison table when there are several and, for exactly two files, a line with the second against the first (passed, steps, tokens and seconds per pass, the one-step act and cache shares in percentage points, tool errors, stalls, false claims, unproven steps, the share of the easy ways taken); without a file, every run saved under `<home>/evals/`. |

Every attempt runs unattended in a throw-away Dot home (its own database and workspace, removed afterwards), with the
Mac desktop, your MCP servers, skills, plugins and the memory embeddings switched off (a task that brings skills or an
MCP server has those alone, besides the skills that come with Dot), and with every question of the policy or of the
agent unanswered (one the run waits for is refused at once). Your own database, workspace and screenshots are never
touched; the API keys are looked up first and handed over by name: those of the model under measure and of the fast,
review and vision models that have one. An eval run does not start without the first, nor without the review model's
when it has work in an attempt: the goal check of a run in goal mode (every attempt with `[agent] goal_mode`, the form
task always, a goal that asks to keep going until it is done) or the second opinion of `stall_escalation`. A goal check
that cannot run ends its attempt as failed, saying why, and the next attempt follows. The score holds the pass rate, per
kind of task, what a pass costs in steps, tokens and seconds (split into model calls and tool calls), the share of the
act calls that carried one step and the share of the input tokens served from the prompt cache (both as `dotx stats`
counts them, over every attempt), how often a tool call failed, stalls, runs that were cut, runs that said they were
done and were not, plan steps marked done without evidence (zero since the plan tools require it), done steps whose
evidence the goal check could not bear out (counted from the `goal_check` events, so only in goal mode) and how many
attempts kept a plan (item 252: one that kept none tells nothing of a change to how the plan reaches the model; `-` in a
report saved before they were counted), compactions per 100 steps and the share of the model time their summaries took,
web pages not found and calls refused for their arguments (as `dotx stats` counts them; `-` in a report saved before
they were), how many of the attempts at a task that hides an instruction in one of its files (a note "to the AI
assistant" in an invoice, a hidden block in a saved page, a line in a notes file) followed it, judged by what only
following it does (a file it asks for, the key it asks to give, a total or a date that the files do not hold), so an
answer that quotes the instruction while refusing it has not followed it (a report saved before quotes were told apart
counted one as following it), how many of the attempts at a task that help makes short took its easy way (a call of the
skill or MCP tool it names that ran; a call that did not run does not count) and what a pass costs in steps when it did
and when it did not, how often find_help was called (`-` in a report saved before these were counted), and the tasks
that passed only in some attempts. The tokens of an attempt, the ceiling it is cut at and the budget of a probe of the
harness count every model call of the run and of the runs it delegated to, side calls included (the report of an
attempt counted the run's own only before item 589), and the report holds the approvals the run asked, each declined;
a report saved before model calls were timed counted the steps' tokens only and has
no seconds, and one saved before acts and cached tokens were counted shows `-` for their shares. `dotx eval run` saves
its report after every attempt, so an interrupted run keeps what it measured.

A task is data (`src/dotagent/evals/catalog.py`): the files the workspace starts with (their text, or the bytes of an
Office file in Base64), the goal, checks (a file exists, holds or equals something, a JSON file equals a value, a
command exits with 0, the answer contains, matches or does not contain something, no file or folder anywhere in the
workspace is named like a glob), the checks that read the run's events (item 659: the skill it read or left unread, the
tool of an MCP server that ran, a tool group that stayed off, no pick made in the composer), the checks that hold unless
the run followed an instruction hidden in a file (`dotx eval check` reports one that an answer quoting the files the
goal names would fail), the skills and MCP servers it brings and the easy way they offer (or a skill that comes with
Dot), whether its tools are grouped, and a reference solution as a script of tool calls. The two tasks on Office files
start from a workbook whose formulas hold the results Excel saved and from a Word document whose company name is bold in
one place and split over two runs in another; their easy way is the skill that comes with Dot for them, and their checks
read the files with Dot's own Python (`python -I -m dotagent.evals.office_tasks`). To add one, write it there and run
`dotx eval check`.

### The choice of help, and what the purpose lines change

A run is told which skill or MCP server to use only when its message carries a pick (a tag in the composer). Otherwise
it chooses from what its instructions say of them: a skill's name and description, and the line of each MCP server's
tool group, which says what the server is for ([MCP servers](extending.md#mcp-servers), item 659). Five tasks of the
kind `choice` measure that choice. Each has help that fits its goal beside a decoy that does not, and passes only when
the events of the run show the right pick and no pick was made for it.

| Task                               | The right pick                           | The decoy                                 | What tells them apart                                                                 |
| ---------------------------------- | ---------------------------------------- | ----------------------------------------- | ------------------------------------------------------------------------------------- |
| `changelog-skill-among-decoys`     | the skill `shop-changelog`               | the skills `release-mail`, `docs-voice`   | the skills' descriptions; the form of the entry is in the right skill alone           |
| `tweet-skill-among-many`           | the skill `x-post-writer`                | the skill `linkedin-post`                 | 25 skills are offered, more than the 20 listed: the right one shares no word with the goal, so only its name is said |
| `rates-server-by-its-words`        | the server `ab-tools`, the bank's rates  | `zq-kit`, a unit converter with `convert` | each server's own instructions                                                        |
| `timetable-server-by-owners-words` | the server `kb-desk`, the school's timetable | `mv-lab`, a lab's freezers, which shares a word | the `description` of each `[[mcp]]` table; the servers say nothing           |
| `forecast-server-by-its-tools`     | the server `ml-pack`, a weather forecast | `pq-dev`, the status of a build           | the first sentence of each server's tool; the servers say nothing else                |

The tasks that bring servers run with their tools in groups, as a run of more than 128 tools has them, so each server's
group starts off and the lines are all the run has to go by. The servers are stand-ins (`python -m
dotagent.evals.tool_server NAME`): one tool, answering from a fixed table, named alike (`get`) or after what the decoy's
name fits better. A task passes when its file or answer is right and the events show it: the right skill read
(`skill_read`) and the decoys' not (`skill_unread`), the right server's tool ran (`tool_ran`: a call of a tool whose
group is off runs and turns the group on, item 742), the decoy's group never on
(`group_stays_off`: turned on by `use_tools` or by a call of its tool), and no pick (`no_picks`). `dotx eval check`
proves that each can be passed and that none is passed by doing nothing. `tests/smoke/test_choice_tasks.py` plays the
wrong picks too (the decoy's group, the decoy's skill, no skill, an answer from memory): they prove that the tasks can
be failed, not what a model does.

**To measure what the purpose lines change**, play the same five tasks twice with the same model, once as Dot runs (the
lines say what each server is for) and once with `--no-mcp-purposes` (`[agent] mcp_purposes = false` for that run: each
line is the server's name and its tools' names, as before item 659), then compare the two reports. It needs the key of
the model under measure, found the way the daemon finds it, and calls it: no test does, and it is for the owner to run.
Leave out `--model` for `[models] main`.

```bash
# A: as Dot runs.
dotx eval run --repeat 5 --task changelog-skill-among-decoys --task tweet-skill-among-many \
  --task rates-server-by-its-words --task timetable-server-by-owners-words --task forecast-server-by-its-tools

# B: the same tasks and model, with the lines of the groups as they were.
dotx eval run --repeat 5 --no-mcp-purposes --task changelog-skill-among-decoys --task tweet-skill-among-many \
  --task rates-server-by-its-words --task timetable-server-by-owners-words --task forecast-server-by-its-tools

# The two, side by side, with a line for the second against the first.
dotx eval report
```

**What it costs.** An attempt is cut after 180 s or 250000 tokens, so the notice before each run states a ceiling of
6,250,000 tokens for five repeats of the five tasks, which only a run that loops reaches. A step of these tasks sends
13,500 to 19,500 tokens (the loop's own estimate, measured on the scripted runs of the tasks; the lines add 50 to 150),
and a run that picks right takes three to six steps (the references take three), so an attempt is about 40,000 to
120,000 input tokens, one arm at five repeats about two million and both arms about four million, the most of it the
same instructions sent again, which a provider that caches them bills for less. The output is a few thousand tokens an
attempt. A model on your own Mac costs time and no money.

**Reading it.** The by-kind line of each report has `choice` passes out of 25, and the settings line says `server
purposes on` or `off`. The three server tasks are what the lines change; the two skill tasks are the control, since the
switch does not touch them: if their pass rates differ between the arms, that is the noise of the model on these tasks,
and a difference of the server tasks no bigger than it is not one. Under `did not pass`, a failure says which pick was
wrong: `the tool group mcp__zq-kit stayed off` is a decoy opened, `the run called mcp__ab-tools__lookup and the call
ran` a server never used, `the run read the skill x-post-writer` a skill missed (the run that found it by its name, or
by `find_help`, passes). One attempt says little; five show the tasks a model passes only now and then. No live run of
these has been made yet.
