//! The installer (item 575): what the desktop app does on a Mac that has no Dot. Its window shows a page bundled with
//! the app (`ui/installer.html`), and one button runs, from here, the install: Python and Dot's package with the `uv`
//! the app carries (`uv tool install`, into `runtime/` of Dot's home), `dotx setup` with the model and the key the
//! person chose, and `dotx service install` with the launcher the app carries, so that neither Homebrew nor Xcode's
//! tools are needed. The app also keeps the package in step with itself: when the app updates and its package is the
//! older, the app upgrades the package it installed.
//!
//! What must hold (`roadmap/575.md`):
//! - Only the installer window's own page may call the commands of this module (`COMMANDS`): Tauri's ACL names them in
//!   the app manifest (`build.rs`) and `capabilities/installer.json` grants them to that window and to local pages.
//! - The page chooses a provider and a model, nothing else. What runs is the bundled `uv` (with `--no-config`), the
//!   Python of the environment `uv` made, running `-m dotagent` (that is `dotx`), and `/usr/bin/open`, with the
//!   arguments the functions below build; the version is the app's own. The sandbox keeps a run's commands from
//!   writing the runtime folder (`_own_code` in `runtime.py`), so that what the app runs at its start is not theirs.
//! - The key goes to `dotx setup --key-stdin` on its standard input and nowhere else: it is a `Secret`, and every line
//!   a step prints is masked before it is kept, shown or written to the log.
//! - It never installs over another Dot (`foreign`, `decide`), and it resumes an install of its own that stopped, from
//!   the first step that is not done. The marker records each step as it finishes (`Marker`, `record`); the install is
//!   complete when its service is installed, and the daemon's first answer decides nothing (`Found::done`), so that
//!   every start, with the daemon up or down, knows what is done without having seen it answer.

use std::ffi::OsString;
use std::fmt;
use std::fs::{self, File, OpenOptions};
use std::io::{self, BufRead, BufReader, Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::{symlink, OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{self, RecvTimeoutError};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Manager, State};

/// The window of the installer and its page. The window `main` never shows the page, and the capability that grants
/// the commands names this label alone.
pub const WINDOW: &str = "installer";
pub const PAGE: &str = "installer.html";

/// The commands the page calls: the names of `build.rs`'s app manifest (each needs the permission `allow-` and its
/// name with dashes), of the handler `lib.rs` registers and of `capabilities/installer.json`; a test keeps the three
/// equal to this list.
#[cfg(test)]
pub const COMMANDS: [&str; 5] = [
    "installer_inspect",
    "installer_status",
    "installer_start",
    "installer_retry",
    "installer_open",
];

const RUNTIME: &str = "runtime";
const MARKER: &str = "app-install.json";
/// The folder in the runtime folder that the service's `PATH` names: two links, to `rg` and `uv` of the app's tools and
/// to nothing else (`ON_PATH`), which the app makes again at every start where the app is, so that moving the app does
/// not lose `rg`, and a file put in the app's tools folder is not on the `PATH`.
const TOOLS_FOLDER: &str = "app-tools";
/// What of the app's tools goes on the service's `PATH`: the search and uv. The launcher is copied, not run from there.
const ON_PATH: [&str; 2] = ["rg", "uv"];
const PACKAGE: &str = "dotagent-macos";
const PYTHON: &str = "3.13";
const LAUNCH_AGENT: &str = "dev.dotagent.dot.plist";
/// The label of the service's launchd job, which `LAUNCH_AGENT` is the property list of (`launchd.py` `LABEL`).
const LABEL: &str = "dev.dotagent.dot";
/// The most the shell reads of a small file that a command of a run may have changed: the marker and the job's property
/// list are a few hundred bytes. A longer file is not read.
const READ_LIMIT: u64 = 64 * 1024;
const LOG_FILE: &str = "installer.log";
const CHROME_APP: &str = "/Applications/Google Chrome.app";
const CHROME_PAGE: &str = "https://www.google.com/chrome/";
const OLLAMA_PAGE: &str = "https://ollama.com/download";
const OLLAMA_TAGS: &str = "http://127.0.0.1:11434/api/tags";
/// macOS 15.2 is the first with the screen capture the desktop tools use.
const MIN_MACOS: (u64, u64) = (15, 2);
const OPEN: &str = "/usr/bin/open";
const SW_VERS: &str = "/usr/bin/sw_vers";
const REQUEST_TIMEOUT: Duration = Duration::from_millis(800);
/// How long each program may run before the installer ends it and says so.
const UV_LIMIT: Duration = Duration::from_secs(30 * 60);
const DOTX_LIMIT: Duration = Duration::from_secs(10 * 60);
const DAEMON_WAIT: Duration = Duration::from_secs(60);
/// What `begin` says to a choice of models sent to an install that was resumed with its models done already.
const MODELS_KEPT: &str = "The models are set up already, and this install goes on without them. Change them in Dot's Settings once Dot is open.";
const TICK: Duration = Duration::from_millis(100);
/// Output that arrives after a program ended (a grandchild that holds the pipe) is read for this long.
const DRAIN: Duration = Duration::from_secs(2);
const LINE_CHARS: usize = 600;
const LINE_READ: u64 = 64 * 1024;
/// The log is started again once it is this long (some runs and retries are a few hundred kilobytes).
const LOG_LIMIT: u64 = 4 * 1024 * 1024;
const KEPT_LINES: usize = 40;
/// The lines of one program kept for the failure's words; the log has them all.
const MAX_RAN_LINES: usize = 2000;
const SHOWN_FAILURE_LINES: usize = 12;
const MASK: &str = "••••";
/// A key shorter than this is not a key (and would mask words); the page asks for at least this many characters.
const KEY_MIN: usize = 8;
const KEY_MAX: usize = 512;
const MODEL_MAX: usize = 128;

/// The providers of the page: what `dotx setup` offers, less OpenCode's own agent (`desktop/src-tauri/providers.json`;
/// `tests/unit/test_installer_files.py` keeps it equal to `CHOICES` of `cli_setup.py`).
const PROVIDERS: &str = include_str!("../providers.json");

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub struct Provider {
    pub provider: String,
    pub label: String,
    pub needs: String,
    /// Whether its models need an API key (Ollama's run on this Mac or with an Ollama account).
    pub key: bool,
    /// Models to suggest, the ones that exist as the file is written; the person may name another.
    #[serde(default)]
    pub models: Vec<String>,
}

pub fn providers() -> Vec<Provider> {
    serde_json::from_str(PROVIDERS)
        .expect("providers.json is the list of the installer's providers")
}

/// A secret the person typed. It has no `Display` and no `Serialize`, and its `Debug` shows nothing, so that no log
/// line, no event and no answer to the page can carry it by accident.
#[derive(Clone, Deserialize, PartialEq, Eq)]
#[serde(transparent)]
pub struct Secret(String);

impl Secret {
    fn expose(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for Secret {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("Secret(..)")
    }
}

/// What the page sends to start: a provider, a model and, for a provider that needs one, a key.
#[derive(Debug, Deserialize)]
pub struct ChoiceInput {
    provider: String,
    model: String,
    #[serde(default)]
    key: Option<Secret>,
}

/// The choice once checked (`Choice::check`).
#[derive(Clone, Debug, PartialEq, Eq)]
struct Choice {
    provider: String,
    model: String,
    key: Option<Secret>,
}

/// Whether `model` can be handed to `dotx setup --model` as a model's name: it starts with a letter or a digit, so that
/// it is never taken for a flag, and holds only what model ids hold.
fn valid_model(model: &str) -> bool {
    let mut chars = model.chars();
    matches!(chars.next(), Some(first) if first.is_ascii_alphanumeric())
        && model.len() <= MODEL_MAX
        && model.chars().all(|c| {
            c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | ':' | '/' | '@' | '+' | '-')
        })
}

/// Whether `key` can be an API key: visible ASCII with no space, of a length a key has.
fn valid_key(key: &str) -> bool {
    (KEY_MIN..=KEY_MAX).contains(&key.len()) && key.chars().all(|c| c.is_ascii_graphic())
}

impl Choice {
    /// The choice the page sent, or why it cannot be taken. The provider is one of the five of `providers.json`, the
    /// model passes `valid_model`, and the key is there exactly when the provider's models need one.
    fn check(input: ChoiceInput, providers: &[Provider]) -> Result<Choice, String> {
        let Some(provider) = providers.iter().find(|p| p.provider == input.provider) else {
            return Err("That provider is not one the installer offers.".to_string());
        };
        let model = input.model.trim().to_string();
        if !valid_model(&model) {
            return Err(
                "Choose the model by its name, as its provider writes it (letters, digits and . _ : / @ + -)."
                    .to_string(),
            );
        }
        let key = input.key.map(|key| Secret(key.expose().trim().to_string()));
        match (&key, provider.key) {
            (None, true) => return Err(format!("{} needs an API key.", provider.label)),
            (Some(_), false) => return Err(format!("{} needs no API key.", provider.label)),
            (Some(key), true) if !valid_key(key.expose()) => {
                return Err(
                    "That does not look like an API key: it has spaces or is too short."
                        .to_string(),
                )
            }
            _ => {}
        }
        Ok(Choice {
            provider: provider.provider.clone(),
            model,
            key,
        })
    }

    /// The words `dotx setup --model` takes.
    fn model_ref(&self) -> String {
        format!("{}:{}", self.provider, self.model)
    }
}

/// `text` with each secret, and the form each takes in an address, replaced by dots.
fn mask(text: &str, secrets: &[&str]) -> String {
    let mut masked = text.to_string();
    for secret in secrets.iter().filter(|secret| secret.len() >= KEY_MIN) {
        masked = masked.replace(secret, MASK);
        let encoded: String = url::form_urlencoded::byte_serialize(secret.as_bytes()).collect();
        if encoded != *secret {
            masked = masked.replace(&encoded, MASK);
        }
    }
    masked
}

/// The folders the runtime lives in, under Dot's home: the tool environment of `uv`, the program links it makes, the
/// Pythons it downloads, the link to the app's tools and the marker.
#[derive(Clone, Debug)]
pub struct Runtime {
    dir: PathBuf,
}

impl Runtime {
    pub fn of(home: &Path) -> Runtime {
        Runtime {
            dir: home.join(RUNTIME),
        }
    }

    fn tool_dir(&self) -> PathBuf {
        self.dir.join("uv-tools")
    }

    fn bin_dir(&self) -> PathBuf {
        self.dir.join("bin")
    }

    fn python_dir(&self) -> PathBuf {
        self.dir.join("python")
    }

    fn tools_folder(&self) -> PathBuf {
        self.dir.join(TOOLS_FOLDER)
    }

    fn marker(&self) -> PathBuf {
        self.dir.join(MARKER)
    }

    /// The Python of the package's environment, which runs Dot's own commands (`python -P -m dotagent`, as the launchd
    /// job runs the daemon): it is inside the environment the sandbox keeps a run's commands from changing, which
    /// `bin/dotx`, a link in a folder of its own, is not.
    fn python(&self) -> PathBuf {
        self.tool_dir().join(PACKAGE).join("bin").join("python")
    }

    /// The folder of the package's metadata `uv` made, whose name says the version installed.
    fn site_packages(&self) -> PathBuf {
        self.tool_dir().join(PACKAGE).join("lib")
    }
}

/// What the app carries in its Resources: `uv`, `rg` and the launcher (`desktop/fetch-tools.sh` puts them there).
#[derive(Clone, Debug)]
pub struct Tools {
    dir: PathBuf,
}

impl Tools {
    fn uv(&self) -> PathBuf {
        self.dir.join("uv")
    }

    fn launcher(&self) -> PathBuf {
        self.dir.join("dot-launcher")
    }

    fn present(&self) -> bool {
        ["uv", "rg", "dot-launcher"].iter().all(|name| {
            fs::metadata(self.dir.join(name))
                .is_ok_and(|meta| meta.is_file() && meta.permissions().mode() & 0o111 != 0)
        })
    }
}

/// The tools the app carries, `None` for a build that has not got them (a development build): it then shows today's
/// window and never the installer.
pub fn bundled(resources: &Path) -> Option<Tools> {
    let tools = Tools {
        dir: resources.join("tools"),
    };
    tools.present().then_some(tools)
}

/// The steps of the install that the marker records, each as it finished: the package installed, the models set up
/// (`dotx setup`) and the service installed (`dotx service install`). The page lists a fourth step, the wait for the
/// daemon's first answer; it is shown to the person and decides nothing, so it is not here.
#[derive(Clone, Copy, Debug, Default, Deserialize, Serialize, PartialEq, Eq)]
pub struct Steps {
    #[serde(default)]
    pub package: bool,
    #[serde(default)]
    pub setup: bool,
    #[serde(default)]
    pub service: bool,
}

impl Steps {
    /// The install is complete once its service is installed: the steps run in this order, so that the service is the
    /// last. Whether the daemon has answered yet, and when, is no part of it.
    pub fn complete(self) -> bool {
        self.package && self.setup && self.service
    }
}

/// The install the app made, as the file in the runtime folder says.
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub struct Marker {
    /// The version of the package `uv` installed.
    pub version: String,
    /// The steps that finished, written as each did. A marker of an older build has none (`None`): what it said in
    /// their place (`"complete": true` once the daemon had answered) is not read, and the disk alone says what is done
    /// (`Found::done`).
    #[serde(default)]
    pub steps: Option<Steps>,
}

/// Open `path` for a file that a command of a run may have put something else at, so that only a plain file is used and
/// nothing waits. `O_NOFOLLOW` refuses a link. `O_NONBLOCK` makes the open of a FIFO (or a device) return at once, and
/// not when somebody opens the other end, which a program that holds the other end never does. What was opened is
/// then checked on the descriptor, which no one can change the object behind, and not on the name, to be a regular
/// file; and the descriptor goes back to blocking. Anything else is an error that says so.
fn open_regular(path: &Path, options: &mut OpenOptions) -> io::Result<File> {
    let file = options
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
        .open(path)?;
    if !file.metadata()?.file_type().is_file() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "it is not a regular file (a FIFO, a folder or a device)",
        ));
    }
    let descriptor = file.as_raw_fd();
    // SAFETY: `descriptor` is the descriptor of `file`, which is open for as long as `file` is, and `fcntl` with
    // `F_GETFL` and `F_SETFL` only reads and sets that descriptor's status flags; it touches no memory.
    let flags = unsafe { libc::fcntl(descriptor, libc::F_GETFL) };
    if flags == -1
        || unsafe { libc::fcntl(descriptor, libc::F_SETFL, flags & !libc::O_NONBLOCK) } == -1
    {
        return Err(io::Error::last_os_error());
    }
    Ok(file)
}

/// What is in a small file that a command of a run may have changed (the marker, the job's property list), or `None`
/// when it is not a regular file (`open_regular`), cannot be read, or is longer than `READ_LIMIT`: such a file is
/// read as no file that the app wrote.
fn read_small(path: &Path) -> Option<Vec<u8>> {
    let file = open_regular(path, OpenOptions::new().read(true)).ok()?;
    let mut bytes = Vec::new();
    file.take(READ_LIMIT + 1).read_to_end(&mut bytes).ok()?;
    (bytes.len() as u64 <= READ_LIMIT).then_some(bytes)
}

fn read_marker(runtime: &Runtime) -> Option<Marker> {
    serde_json::from_slice(&read_small(&runtime.marker())?).ok()
}

/// Whether an install of the app's own stopped before its end: it has a marker and a step is not done.
fn resumes(found: &Found) -> bool {
    found.done().is_some_and(|steps| !steps.complete())
}

fn write_marker(runtime: &Runtime, marker: &Marker) -> io::Result<()> {
    fs::create_dir_all(&runtime.dir)?;
    let staging = runtime.dir.join(format!(".{MARKER}.tmp"));
    // Made new, as a file of its own: what is at that name (a link a program of another kind left) is removed first and
    // never written through.
    remove_entry(&staging)?;
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(&staging)?;
    file.write_all(
        serde_json::to_string(marker)
            .expect("a marker is JSON")
            .as_bytes(),
    )?;
    drop(file);
    fs::rename(&staging, runtime.marker())
}

/// Record in the marker that `step` finished, as it finishes: the marker is written again with the steps done so far
/// (what it recorded and what the disk shows, `Found::done`) and this one, so that it says at every moment how far the
/// install is. The package step makes the marker, with the version `uv` installed; the others keep that version.
fn record(ctx: &Context, step: usize) -> io::Result<()> {
    let seen = found(&ctx.home, &ctx.user_home, false);
    let mut steps = seen.done().unwrap_or_default();
    match step {
        PACKAGE_STEP => steps.package = true,
        MODELS_STEP => steps.setup = true,
        SERVICE_STEP => steps.service = true,
        _ => {}
    }
    let version = match &seen.marker {
        Some(marker) if step != PACKAGE_STEP => marker.version.clone(),
        _ => ctx.version.clone(),
    };
    write_marker(
        &ctx.runtime(),
        &Marker {
            version,
            steps: Some(steps),
        },
    )
}

/// Whose the launchd job is, if there is one.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Job {
    None,
    /// Its property list is the app's own job, as `dotx service install` writes it when the app runs it
    /// (`is_the_apps_job`): the label of Dot's service, Dot.app's program in Dot's home run with the Python of this
    /// runtime folder's package environment. What merely mentions the runtime folder is not.
    Ours,
    Other,
}

/// What the shell finds of Dot on this Mac.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Found {
    pub marker: Option<Marker>,
    pub job: Job,
    pub daemon_answers: bool,
    /// `config.toml` is in Dot's home.
    pub config: bool,
    /// The token file is in Dot's home: a daemon has run with it.
    pub token: bool,
    /// The Python of the package's environment is in the runtime folder: the package is installed.
    pub package: bool,
}

impl Found {
    /// What of the app's own install is done, `None` where there is none (no marker). One function says it, for the
    /// start of the app, each step and the upgrade.
    ///
    /// The marker records each step as it finished, and what is on the disk adds to that: the models are done where a
    /// `config.toml` is (`dotx setup` is never run over one), the service where the launchd job is the app's own, and
    /// the package only while its Python is there. A marker an older build wrote records no steps, and what it said
    /// instead (whether the daemon had answered) is a claim like any other in a file that a command may have written, so
    /// it is not believed: such a marker is read by the disk alone, and a step is done only where the disk shows it.
    pub fn done(&self) -> Option<Steps> {
        let marker = self.marker.as_ref()?;
        Some(match marker.steps {
            Some(recorded) => Steps {
                package: recorded.package && self.package,
                setup: recorded.setup || self.config,
                service: recorded.service || self.job == Job::Ours,
            },
            None => Steps {
                package: self.package,
                setup: self.config,
                service: self.job == Job::Ours,
            },
        })
    }
}

/// What the window shows: the installer, or what it showed before (the UI, or the page that says Dot is down).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Decision {
    Installer,
    Dot,
}

/// Why the installer does not run: another Dot is here.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Reason {
    DaemonAnswers,
    ServiceInstalled,
    Config,
    Token,
    InstallComplete,
}

impl Reason {
    fn words(self) -> &'static str {
        match self {
            Reason::DaemonAnswers => "a Dot daemon already answers on this Mac",
            Reason::ServiceInstalled => "Dot's service is already installed on this Mac",
            Reason::Config => "Dot's home already has a config.toml",
            Reason::Token => "Dot's home already has a token: a daemon has run from it",
            Reason::InstallComplete => "Dot is installed on this Mac",
        }
    }
}

/// Whether another Dot is here, and which sign of it: the installer never runs over it. A Dot that is not the app's
/// own is a daemon that answers, a launchd job that is another program's, or, with no marker of the app's, a job, a
/// `config.toml` or a token file. An install of the app's own (its marker, written once `uv` succeeded) is not another
/// Dot, finished or not. Asked before each step of the install, so that a Dot that appears while the page is open
/// stops it.
pub fn foreign(found: &Found) -> Option<Reason> {
    if found.daemon_answers {
        return Some(Reason::DaemonAnswers);
    }
    match &found.marker {
        Some(_) if found.job == Job::Other => Some(Reason::ServiceInstalled),
        Some(_) => None,
        None if found.job != Job::None => Some(Reason::ServiceInstalled),
        None if found.config => Some(Reason::Config),
        None if found.token => Some(Reason::Token),
        None => None,
    }
}

/// Whether the installer may be what the window shows at the start, and if not, why: another Dot is here
/// (`foreign`), or the app's own install is complete (`Found::done`: its service is installed). What exists makes the
/// app show today's window, the UI, or the page that says Dot is down where the daemon does not answer; an install of
/// the app's own with a step missing is resumed from that step.
pub fn blocker(found: &Found) -> Option<Reason> {
    foreign(found).or_else(|| {
        found
            .done()
            .is_some_and(Steps::complete)
            .then_some(Reason::InstallComplete)
    })
}

pub fn decide(found: &Found) -> Decision {
    match blocker(found) {
        None => Decision::Installer,
        Some(_) => Decision::Dot,
    }
}

/// The program launchd runs for the app's own job: Dot.app in Dot's home, which `dotx service install` makes from the
/// launcher the app carries and which starts the daemon's Python and waits for it (`dot_app.py`).
fn launcher_of(home: &Path) -> PathBuf {
    home.join("Dot.app")
        .join("Contents")
        .join("MacOS")
        .join("Dot")
}

/// Whether the property list `bytes` is the app's own job, read as launchd reads it and not as text: a dictionary whose
/// label is Dot's service, which names no `Program` of its own (launchd would run that, not the first word), and whose
/// `ProgramArguments` begin with Dot.app's program in Dot's home and, exactly, the Python of this runtime folder's
/// package environment (`dotx service install` writes `[launcher, python, -P, -m, dotagent, daemon]`). A list that
/// merely mentions the runtime folder, in a comment, a key or the `PATH` of a checkout's job, is not it, nor is a job
/// of another program that has the runtime's Python for an argument. Paths are compared as paths (`/a//b/` is `/a/b`),
/// never resolved: `..` stays `..`.
fn is_the_apps_job(bytes: &[u8], home: &Path, runtime: &Runtime) -> bool {
    let Ok(plist::Value::Dictionary(job)) = plist::Value::from_reader(io::Cursor::new(bytes))
    else {
        return false;
    };
    if job.get("Label").and_then(plist::Value::as_string) != Some(LABEL)
        || job.contains_key("Program")
    {
        return false;
    }
    let Some(arguments) = job
        .get("ProgramArguments")
        .and_then(plist::Value::as_array)
        .and_then(|words| {
            words
                .iter()
                .map(plist::Value::as_string)
                .collect::<Option<Vec<&str>>>()
        })
    else {
        return false;
    };
    matches!(
        arguments.as_slice(),
        [launcher, python, ..]
            if Path::new(launcher) == launcher_of(home) && Path::new(python) == runtime.python()
    )
}

/// What the shell finds in Dot's `home`, in the user's `user_home` (the launchd agents) and at the daemon's address
/// (`daemon_answers`, asked by the caller). A property list that cannot be read, or that is a link or another kind of
/// file than a plain one, is another Dot's.
pub fn found(home: &Path, user_home: &Path, daemon_answers: bool) -> Found {
    let runtime = Runtime::of(home);
    let plist = user_home
        .join("Library")
        .join("LaunchAgents")
        .join(LAUNCH_AGENT);
    let job = match plist.symlink_metadata() {
        Err(error) if error.kind() == io::ErrorKind::NotFound => Job::None,
        _ if read_small(&plist).is_some_and(|bytes| is_the_apps_job(&bytes, home, &runtime)) => {
            Job::Ours
        }
        _ => Job::Other,
    };
    Found {
        marker: read_marker(&runtime),
        job,
        daemon_answers,
        config: home.join("config.toml").exists(),
        token: home.join("api-token").exists(),
        package: runtime.python().exists(),
    }
}

/// Whether `version` is older than `app`: both read as `x.y.z` (a release has no other form), a pre-release suffix
/// (`rc1`, `.dev3`) ranks below the same numbers without one. A version that does not read is never the older: the
/// app upgrades only what it can compare.
pub fn older(installed: &str, app: &str) -> bool {
    fn read(version: &str) -> Option<((u64, u64, u64), bool)> {
        let mut numbers = [0u64; 3];
        let mut pre = false;
        let mut parts = version.trim().split('.');
        for slot in &mut numbers {
            let part = parts.next()?;
            let digits: String = part.chars().take_while(char::is_ascii_digit).collect();
            if digits.is_empty() {
                return None;
            }
            *slot = digits.parse().ok()?;
            pre |= digits.len() != part.len();
        }
        pre |= parts.next().is_some();
        Some(((numbers[0], numbers[1], numbers[2]), pre))
    }
    match (read(installed), read(app)) {
        (Some((a, a_pre)), Some((b, b_pre))) => a < b || (a == b && a_pre && !b_pre),
        _ => false,
    }
}

/// The version of the package installed in the runtime, from the name of the metadata folder `uv` made
/// (`dotagent_macos-0.1.0.dist-info`), without starting Python.
fn installed_version(runtime: &Runtime) -> Option<String> {
    let lib = fs::read_dir(runtime.site_packages()).ok()?;
    for python in lib.flatten() {
        let Ok(packages) = fs::read_dir(python.path().join("site-packages")) else {
            continue;
        };
        for entry in packages.flatten() {
            let name = entry.file_name();
            let name = name.to_string_lossy();
            if let Some(version) = name
                .strip_prefix("dotagent_macos-")
                .and_then(|rest| rest.strip_suffix(".dist-info"))
            {
                return Some(version.to_string());
            }
        }
    }
    None
}

/// A program to run: what the installer may run is only what the functions below build.
#[derive(Debug)]
pub struct Plan {
    program: PathBuf,
    args: Vec<OsString>,
    env: Vec<(String, String)>,
    /// What goes on the program's standard input, the key for `--key-stdin`.
    stdin: Option<Secret>,
    limit: Duration,
}

impl Plan {
    /// The whole environment the program is started with: the few variables it inherits and what the plan sets.
    fn environment(
        &self,
        inherited: impl IntoIterator<Item = (OsString, OsString)>,
    ) -> Vec<(OsString, OsString)> {
        environment_of(&self.env, inherited)
    }

    /// The words the log and the page show for it. The key is not in them: it is not an argument.
    fn words(&self) -> String {
        let mut words = vec![self
            .program
            .file_name()
            .map_or_else(String::new, |name| name.to_string_lossy().into_owned())];
        words.extend(
            self.args
                .iter()
                .map(|arg| arg.to_string_lossy().into_owned()),
        );
        words.join(" ")
    }
}

/// What a step sets for its program, on top of the few variables it inherits (`INHERITED`). The `PATH` has the two
/// links of the app's tools (`rg`, `uv`), the runtime's programs and the system's, and no Homebrew, so that the install
/// is the same on every Mac.
fn child_env(runtime: &Runtime, tools: &Tools) -> Vec<(String, String)> {
    let path = format!(
        "{}:{}:/usr/bin:/bin:/usr/sbin:/sbin",
        runtime.tools_folder().display(),
        runtime.bin_dir().display()
    );
    vec![
        ("PATH".to_string(), path),
        (
            "UV_TOOL_DIR".to_string(),
            runtime.tool_dir().display().to_string(),
        ),
        (
            "UV_TOOL_BIN_DIR".to_string(),
            runtime.bin_dir().display().to_string(),
        ),
        (
            "UV_PYTHON_INSTALL_DIR".to_string(),
            runtime.python_dir().display().to_string(),
        ),
        // Only the Pythons uv downloads here: one of this Mac's would be gone when Homebrew upgrades it.
        (
            "UV_PYTHON_PREFERENCE".to_string(),
            "only-managed".to_string(),
        ),
        // What uv downloads stays in the runtime folder too, so that removing the folder removes it all.
        (
            "UV_CACHE_DIR".to_string(),
            runtime.dir.join("cache").display().to_string(),
        ),
        ("UV_NO_PROGRESS".to_string(), "1".to_string()),
        ("NO_COLOR".to_string(), "1".to_string()),
        // What Dot's own commands print comes to the page as it is printed, not when they end.
        ("PYTHONUNBUFFERED".to_string(), "1".to_string()),
        // `dotx` reads these two: the launcher Dot.app is made of, and the folder that goes on the service's PATH.
        (
            "DOT_APP_LAUNCHER".to_string(),
            tools.launcher().display().to_string(),
        ),
        (
            "DOT_APP_TOOLS".to_string(),
            runtime.tools_folder().display().to_string(),
        ),
    ]
}

/// The variables of the app's own environment that a program of the installer inherits, by name, and no others: the
/// ones that tell it its home, its temporary folder and its language, the names of Dot's own folders, and what uv needs
/// behind a proxy or a company's certificate (in both cases, as programs read them). The rest of the environment (uv's
/// and Python's settings, `__PYVENV_LAUNCHER__`, `NODE_OPTIONS`, `NETRC`, `DYLD_*`, whatever a session set with
/// `launchctl setenv`) is not passed on, so that what a step runs is what the step says.
const INHERITED: [&str; 20] = [
    "HOME",
    "USER",
    "LOGNAME",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "DOT_HOME",
    "DOT_LOG_DIR",
    "DOT_CHROME_DIR",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "NO_PROXY",
    "no_proxy",
    "ALL_PROXY",
    "all_proxy",
];

/// The whole environment of a program of the installer: what it inherits of `inherited` (`INHERITED`), then what the
/// plan sets, which wins. The program is started with this and nothing else (`env_clear`).
fn environment_of(
    plan_env: &[(String, String)],
    inherited: impl IntoIterator<Item = (OsString, OsString)>,
) -> Vec<(OsString, OsString)> {
    let mut environment: Vec<(OsString, OsString)> = inherited
        .into_iter()
        .filter(|(name, _)| {
            INHERITED
                .iter()
                .any(|kept| name.as_bytes() == kept.as_bytes())
        })
        .collect();
    for (name, value) in plan_env {
        environment.retain(|(have, _)| have.as_bytes() != name.as_bytes());
        environment.push((name.into(), value.into()));
    }
    environment
}

/// `uv tool install` of the package at the app's own version: `reinstall` for an upgrade. `--no-config` keeps uv from
/// reading a `uv.toml` (the person's own, under `~/.config/uv`, which a command of a run could write): where the package
/// comes from is the app's to say, not a file's.
fn uv_install(tools: &Tools, runtime: &Runtime, version: &str, reinstall: bool) -> Plan {
    let mut args: Vec<OsString> = vec!["--no-config".into(), "tool".into(), "install".into()];
    if reinstall {
        args.push("--reinstall".into());
    }
    args.extend([
        "--python".into(),
        PYTHON.into(),
        format!("{PACKAGE}=={version}").into(),
    ]);
    Plan {
        program: tools.uv(),
        args,
        env: child_env(runtime, tools),
        stdin: None,
        limit: UV_LIMIT,
    }
}

/// A command of Dot's own, `dotx <words>`, run as the launchd job runs the daemon: by the Python of the package's
/// environment, with `-P` (the working folder is not put on the path Python imports from) and `-m dotagent`.
fn dotx(tools: &Tools, runtime: &Runtime, words: Vec<OsString>, stdin: Option<Secret>) -> Plan {
    let mut args: Vec<OsString> = vec!["-P".into(), "-m".into(), "dotagent".into()];
    args.extend(words);
    Plan {
        program: runtime.python(),
        args,
        env: child_env(runtime, tools),
        stdin,
        limit: DOTX_LIMIT,
    }
}

/// `dotx setup` for the choice, leaving the service as it is (the next step installs it with the app's launcher). The
/// key, when there is one, is the program's standard input (`--key-stdin`), never an argument.
fn dotx_setup(tools: &Tools, runtime: &Runtime, choice: &Choice) -> Plan {
    let mut words: Vec<OsString> = vec![
        "setup".into(),
        "--model".into(),
        choice.model_ref().into(),
        "--no-service".into(),
    ];
    if choice.key.is_some() {
        words.push("--key-stdin".into());
    }
    dotx(tools, runtime, words, choice.key.clone())
}

/// `dotx service install` with the launcher the app carries in place of the one clang builds.
fn dotx_service(tools: &Tools, runtime: &Runtime) -> Plan {
    let words = vec![
        "service".into(),
        "install".into(),
        "--launcher".into(),
        tools.launcher().into(),
    ];
    dotx(tools, runtime, words, None)
}

/// `dotx service restart --when-idle`: the daemon starts again once no run works, with the package just upgraded.
fn dotx_restart(tools: &Tools, runtime: &Runtime) -> Plan {
    let words = vec!["service".into(), "restart".into(), "--when-idle".into()];
    dotx(tools, runtime, words, None)
}

/// Remove what is at `path`, whatever it is: a link is removed, never followed; a folder goes with what it holds.
fn remove_entry(path: &Path) -> io::Result<()> {
    match path.symlink_metadata() {
        Ok(meta) if meta.file_type().is_dir() => fs::remove_dir_all(path),
        Ok(_) => fs::remove_file(path),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(error),
    }
}

/// Make the folder of the service's `PATH` hold the two links of the app's tools (`ON_PATH`: `rg` and `uv`), where the
/// app is now, and nothing else. Done at every start of the app and before the steps that need `rg`, so that a moved
/// app does not leave the `PATH` pointing at nothing, and what the app's tools folder holds beside the three programs
/// (a file a release should never have carried) is not on the `PATH` of a daemon that holds a token. A link or a file
/// where the folder belongs (the first design made one link to the whole tools folder) is replaced by a folder, and
/// nothing is written through it. A link that is right is left alone, and each one that is not is replaced in one step.
fn link_tools(runtime: &Runtime, tools: &Tools) -> io::Result<()> {
    let folder = runtime.tools_folder();
    match folder.symlink_metadata() {
        Ok(meta) if meta.file_type().is_dir() => {}
        Ok(_) => {
            fs::remove_file(&folder)?;
            fs::create_dir(&folder)?;
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            fs::create_dir_all(&runtime.dir)?;
            fs::create_dir(&folder)?;
        }
        Err(error) => return Err(error),
    }
    for name in ON_PATH {
        let target = tools.dir.join(name);
        let link = folder.join(name);
        if fs::read_link(&link).is_ok_and(|current| current == target) {
            continue;
        }
        let staging = folder.join(format!(".{name}.new"));
        remove_entry(&staging)?;
        symlink(&target, &staging)?;
        // A folder where the link belongs cannot be replaced by a rename: it goes first.
        if link
            .symlink_metadata()
            .is_ok_and(|meta| meta.file_type().is_dir())
        {
            remove_entry(&link)?;
        }
        fs::rename(&staging, &link)?;
    }
    for entry in fs::read_dir(&folder)? {
        let entry = entry?;
        if !ON_PATH.iter().any(|name| entry.file_name() == *name) {
            remove_entry(&entry.path())?;
        }
    }
    Ok(())
}

/// The installer's log, in Dot's log folder: what each step ran and what it printed, masked.
pub struct Log {
    path: PathBuf,
    file: Mutex<Option<File>>,
}

impl Log {
    /// Open the log, or go on without one: Dot's log folder is a place a command of a run may write, so what is at the
    /// log's name is not trusted (`open_regular`). A link (to a file the person owns) is not followed, and a FIFO, which
    /// would hold the app at its first line until a program that never comes reads it, or any file that is not a plain
    /// one, is refused in words, and the install or the upgrade is not kept waiting for a log.
    fn open(dir: &Path) -> Log {
        let path = dir.join(LOG_FILE);
        let file = fs::create_dir_all(dir)
            .and_then(|()| {
                open_regular(
                    &path,
                    OpenOptions::new().create(true).append(true).mode(0o600),
                )
            })
            .and_then(|file| {
                // Started again once it is too long: cut through the descriptor that was checked, not through the name.
                if file.metadata()?.len() > LOG_LIMIT {
                    file.set_len(0)?;
                }
                Ok(file)
            })
            .map_err(|error| match error.raw_os_error() {
                Some(libc::ELOOP) => {
                    eprintln!("dot-shell: the installer's log is a link, which is not followed");
                }
                Some(libc::ENXIO) => eprintln!(
                    "dot-shell: the installer's log is a FIFO or a device with nothing at its other end, so nothing is written to it"
                ),
                _ => eprintln!("dot-shell: the installer's log could not be opened: {error}"),
            })
            .ok();
        Log {
            path,
            file: Mutex::new(file),
        }
    }

    fn line(&self, text: &str) {
        let mut file = self
            .file
            .lock()
            .expect("nothing panics while it holds the log");
        if let Some(file) = file.as_mut() {
            let _ = writeln!(file, "{} {text}", utc_stamp());
        }
    }
}

/// `YYYY-MM-DDTHH:MM:SSZ` of now.
fn utc_stamp() -> String {
    civil(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map_or(0, |since| since.as_secs()),
    )
}

/// `YYYY-MM-DDTHH:MM:SSZ` of `seconds` since 1970 (the days turned into a date as civil calendars count them).
fn civil(seconds: u64) -> String {
    let (days, rest) = (seconds / 86_400, seconds % 86_400);
    let shifted = i64::try_from(days).unwrap_or(0) + 719_468;
    let era = shifted.div_euclid(146_097);
    let day_of_era = shifted.rem_euclid(146_097);
    let year_of_era =
        (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let month_index = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * month_index + 2) / 5 + 1;
    let month = if month_index < 10 {
        month_index + 3
    } else {
        month_index - 9
    };
    let year = year_of_era + era * 400 + i64::from(month <= 2);
    format!(
        "{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}Z",
        rest / 3600,
        rest % 3600 / 60,
        rest % 60
    )
}

/// How a program ended.
#[derive(Debug, PartialEq, Eq)]
enum Ended {
    Exited(i32),
    /// Stopped by a signal, or by the installer.
    Killed,
    TimedOut,
    Cancelled,
    /// It could not be started: the system's words.
    NotStarted(String),
}

/// The lines a program printed, masked, and how it ended.
struct Ran {
    ended: Ended,
    lines: Vec<String>,
}

/// One line read from a program's output, lossy UTF-8 and whole: `run` masks it and then cuts it (`shown`), so that a
/// key that lies across the cut is not left half in the log. A line with no end is read in pieces of at most
/// `LINE_READ` bytes, so that a program that prints without stopping cannot fill the memory.
fn read_lines(source: impl Read + Send + 'static, send: mpsc::Sender<String>) {
    thread::spawn(move || {
        let mut reader = BufReader::new(source);
        let mut bytes = Vec::new();
        loop {
            bytes.clear();
            match (&mut reader).take(LINE_READ).read_until(b'\n', &mut bytes) {
                Ok(0) | Err(_) => return,
                Ok(_) => {
                    let text = String::from_utf8_lossy(&bytes);
                    let line: String = text.trim_end_matches(['\n', '\r']).to_string();
                    if send.send(line).is_err() {
                        return;
                    }
                }
            }
        }
    });
}

/// `line` cut at `LINE_CHARS` characters. It is cut after it is masked, never before: a key across the cut would be
/// left in part.
fn shown(line: &str) -> String {
    line.chars().take(LINE_CHARS).collect()
}

/// Run `plan` in `cwd`: the key on its standard input, its output masked and written to the log and handed to `seen`
/// line by line, and the program ended when it runs past its limit or `cancel` is set.
fn run(plan: &Plan, cwd: &Path, log: &Log, cancel: &AtomicBool, mut seen: impl FnMut(&str)) -> Ran {
    let secrets: Vec<&str> = plan.stdin.iter().map(Secret::expose).collect();
    let mut lines = Vec::new();
    log.line(&format!("$ {}", plan.words()));
    let mut command = Command::new(&plan.program);
    command
        .args(&plan.args)
        .current_dir(cwd)
        .stdin(if plan.stdin.is_some() {
            Stdio::piped()
        } else {
            Stdio::null()
        })
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    command.env_clear();
    command.envs(plan.environment(std::env::vars_os()));
    let mut child: Child = match command.spawn() {
        Ok(child) => child,
        Err(error) => {
            let words = mask(&error.to_string(), &secrets);
            log.line(&format!(
                "could not start {}: {words}",
                plan.program.display()
            ));
            return Ran {
                ended: Ended::NotStarted(words),
                lines,
            };
        }
    };
    if let (Some(secret), Some(mut stdin)) = (&plan.stdin, child.stdin.take()) {
        // The program may end before it reads the key; its exit status then says why.
        let _ = stdin.write_all(secret.expose().as_bytes());
        let _ = stdin.write_all(b"\n");
    }
    let (send, receive) = mpsc::channel::<String>();
    if let Some(out) = child.stdout.take() {
        read_lines(out, send.clone());
    }
    if let Some(err) = child.stderr.take() {
        read_lines(err, send.clone());
    }
    drop(send);
    let deadline = Instant::now() + plan.limit;
    let mut status = None;
    let mut stopped = None;
    let mut ended_at: Option<Instant> = None;
    let mut all_read = false;
    loop {
        if all_read {
            if status.is_none() {
                thread::sleep(TICK);
            }
        } else {
            match receive.recv_timeout(TICK) {
                Ok(line) => {
                    let line = shown(&mask(&line, &secrets));
                    log.line(&line);
                    seen(&line);
                    lines.push(line);
                    if lines.len() > MAX_RAN_LINES {
                        lines.drain(..MAX_RAN_LINES / 4);
                    }
                }
                Err(RecvTimeoutError::Timeout) => {}
                Err(RecvTimeoutError::Disconnected) => all_read = true,
            }
        }
        if status.is_none() {
            status = match child.try_wait() {
                Ok(status) => status,
                Err(_) => child.wait().ok(),
            };
            if status.is_some() {
                ended_at = Some(Instant::now());
            } else if stopped.is_none() && cancel.load(Ordering::SeqCst) {
                let _ = child.kill();
                stopped = Some(Ended::Cancelled);
            } else if stopped.is_none() && Instant::now() >= deadline {
                let _ = child.kill();
                stopped = Some(Ended::TimedOut);
            }
        }
        // The program has ended, and its output is read to the end, or what a grandchild still writes is waited for
        // no longer than `DRAIN`.
        if status.is_some() && (all_read || ended_at.is_some_and(|at| at.elapsed() >= DRAIN)) {
            break;
        }
    }
    let ended = match (stopped, status) {
        (Some(ended), _) => ended,
        (None, Some(status)) => status.code().map_or(Ended::Killed, Ended::Exited),
        (None, None) => Ended::Killed,
    };
    log.line(&format!("ended: {ended:?}"));
    Ran { ended, lines }
}

/// The steps, in order: what the page lists and what runs.
const STEPS: [(&str, &str); 4] = [
    ("package", "Install Dot's engine"),
    ("models", "Set up the models"),
    ("service", "Start Dot at every login"),
    ("daemon", "Wait for Dot to answer"),
];
const PACKAGE_STEP: usize = 0;
const MODELS_STEP: usize = 1;
const SERVICE_STEP: usize = 2;
const DAEMON_STEP: usize = 3;

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum StepState {
    Waiting,
    Running,
    Done,
    Failed,
}

#[derive(Clone, Copy, Debug, Serialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Phase {
    Idle,
    Running,
    Failed,
    Done,
}

#[derive(Clone, Debug, Serialize)]
pub struct StepView {
    id: &'static str,
    title: &'static str,
    state: StepState,
    /// The last lines the step printed, masked.
    output: Vec<String>,
}

/// What stopped the install: the step, what happened, what it printed (masked) and what to do.
#[derive(Clone, Debug, Serialize)]
pub struct Failure {
    step: &'static str,
    title: &'static str,
    message: String,
    output: Vec<String>,
    fix: String,
}

/// What the page polls. It has no field for the key.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Status {
    phase: Phase,
    steps: Vec<StepView>,
    failure: Option<Failure>,
    /// A new choice of models would be taken in this session. It is not where the models were set up before the session
    /// and no choice was made in it (an install that was resumed): Change the models is not offered there, and the
    /// shell refuses such a choice in words (`begin`).
    can_change_models: bool,
    log: String,
}

struct Inner {
    states: [StepState; 4],
    output: [Vec<String>; 4],
    failure: Option<Failure>,
    phase: Phase,
    choice: Option<Choice>,
}

impl Inner {
    fn new() -> Inner {
        Inner {
            states: [StepState::Waiting; 4],
            output: Default::default(),
            failure: None,
            phase: Phase::Idle,
            choice: None,
        }
    }
}

struct Shared {
    inner: Mutex<Inner>,
    running: AtomicBool,
    cancel: AtomicBool,
}

impl Shared {
    fn with<T>(&self, change: impl FnOnce(&mut Inner) -> T) -> T {
        change(
            &mut self
                .inner
                .lock()
                .expect("nothing panics while it holds the install's state"),
        )
    }
}

/// What the commands need to know of this app and this Mac.
#[derive(Clone, Debug)]
pub struct Context {
    pub tools: Tools,
    pub home: PathBuf,
    pub user_home: PathBuf,
    /// The app's version, which is the version of the package it installs.
    pub version: String,
    pub log_dir: PathBuf,
    /// The folder of the app: `Dot.app`.
    pub app: Option<PathBuf>,
    /// Whether a Dot daemon answers where Dot's home says it listens (`daemon_answers`; a test puts another here).
    pub answers: fn(&Path) -> bool,
    /// How long the last step waits for the daemon's first answer (`DAEMON_WAIT`; a test shortens it).
    pub daemon_wait: Duration,
}

impl Context {
    fn runtime(&self) -> Runtime {
        Runtime::of(&self.home)
    }

    fn found(&self) -> Found {
        found(&self.home, &self.user_home, (self.answers)(&self.home))
    }
}

/// The installer, one for the app: managed by Tauri, called by the page through the commands below. A copy is another
/// handle on the same install.
#[derive(Clone)]
pub struct Installer {
    context: Option<Arc<Context>>,
    shared: Arc<Shared>,
}

/// What kept a step from its end: the words, the lines it printed and what to do.
struct Stop {
    message: String,
    output: Vec<String>,
    fix: String,
}

impl Stop {
    fn plain(message: impl Into<String>, fix: impl Into<String>) -> Stop {
        Stop {
            message: message.into(),
            output: Vec::new(),
            fix: fix.into(),
        }
    }
}

/// Whether `lines` say any of `needles`, in any letter case.
fn says(lines: &[String], needles: &[&str]) -> bool {
    lines.iter().any(|line| {
        let lower = line.to_lowercase();
        needles.iter().any(|needle| lower.contains(needle))
    })
}

/// What to do when `ran` did not end well, by the step and what it printed.
fn stop_of(step: usize, what: &str, ran: Ran, ctx: &Context) -> Stop {
    let message = match &ran.ended {
        // The install is complete when this step is reached (its service is installed): only the daemon is late.
        Ended::TimedOut if step == DAEMON_STEP => {
            "Dot is installed, but its daemon has not answered yet.".to_string()
        }
        Ended::Exited(code) => format!("{what} stopped with exit code {code}."),
        Ended::Killed => format!("{what} was ended by the system."),
        Ended::TimedOut => format!("{what} did not finish in time and was ended."),
        Ended::Cancelled => format!("{what} was ended: Dot was closed."),
        Ended::NotStarted(why) => format!("{what} could not be started: {why}"),
    };
    let fix = match step {
        PACKAGE_STEP if says(&ran.lines, &["no solution found", "not found in the package registry", "no matching distribution"]) => {
            format!(
                "PyPI does not have Dot {} yet. A version reaches PyPI some minutes before its app is published: press Try again in ten minutes.",
                ctx.version
            )
        }
        PACKAGE_STEP => "Check the internet connection and press Try again. The log has all that uv printed.".to_string(),
        MODELS_STEP if says(&ran.lines, &["does not list"]) => {
            "That provider does not serve this model: go back and choose another, or write its name as the provider does.".to_string()
        }
        MODELS_STEP if says(&ran.lines, &["could not be listed", "api key", "unauthorized", "401"]) => {
            "The provider did not take the key or did not answer: go back and check the key and the network, or press Try again.".to_string()
        }
        MODELS_STEP => "Go back to check the model and the key, or press Try again. The log has all that was printed.".to_string(),
        SERVICE_STEP => "Press Try again. If it fails again, the log has what launchd and Dot printed.".to_string(),
        _ => format!(
            "The first start can wait for macOS to ask whether Dot may use a folder. This window opens Dot by itself once the daemon answers; press Try again to wait once more, or read daemon.err.log in {}.",
            ctx.log_dir.display()
        ),
    };
    let skip = ran.lines.len().saturating_sub(SHOWN_FAILURE_LINES);
    Stop {
        message,
        output: ran.lines.into_iter().skip(skip).collect(),
        fix,
    }
}

/// Run one plan as a step, recording what it prints for the page.
fn run_step(
    ctx: &Context,
    shared: &Shared,
    log: &Log,
    step: usize,
    what: &str,
    plan: &Plan,
) -> Result<Vec<String>, Stop> {
    let ran = run(plan, &ctx.runtime().dir, log, &shared.cancel, |line| {
        let shown: String = line.chars().take(300).collect();
        shared.with(|inner| {
            let output = &mut inner.output[step];
            output.push(shown);
            if output.len() > KEPT_LINES {
                output.remove(0);
            }
        });
    });
    if ran.ended == Ended::Exited(0) {
        Ok(ran.lines)
    } else {
        Err(stop_of(step, what, ran, ctx))
    }
}

/// The folder of links to the app's tools that the service's `PATH` begins with, made where the app is now: the models
/// step and the service step need it, and either may be the first this session runs.
fn link(ctx: &Context, runtime: &Runtime) -> Result<(), Stop> {
    link_tools(runtime, &ctx.tools).map_err(|error| {
        Stop::plain(
            format!("The link to the app's tools could not be made: {error}"),
            "Press Try again; if it fails again, move Dot to Applications and open it from there.",
        )
    })
}

/// One of the four steps. `choice` is what the person chose, which the models step alone needs; `answered` says whether
/// Dot's daemon answers. A step of the first three that ends well is recorded in the marker at once (`record`), which is
/// all that decides whether the install is complete: the fourth waits for the daemon's first answer, so that the page
/// can say Dot is ready, and decides nothing.
fn do_step(
    ctx: &Context,
    shared: &Shared,
    log: &Log,
    step: usize,
    choice: Option<&Choice>,
    answered: &dyn Fn() -> bool,
) -> Result<(), Stop> {
    let runtime = ctx.runtime();
    fs::create_dir_all(&runtime.dir).map_err(|error| {
        Stop::plain(
            format!(
                "Dot's folder {} could not be made: {error}",
                runtime.dir.display()
            ),
            "Check that you may write in your Library folder, then press Try again.",
        )
    })?;
    let recorded = |step: usize| {
        record(ctx, step).map_err(|error| {
            Stop::plain(
                format!("The install's marker could not be written: {error}"),
                "Check that you may write in Dot's folder, then press Try again.",
            )
        })
    };
    match step {
        PACKAGE_STEP => {
            run_step(
                ctx,
                shared,
                log,
                step,
                "uv tool install",
                &uv_install(&ctx.tools, &runtime, &ctx.version, false),
            )?;
            if !runtime.python().exists() {
                return Err(Stop::plain(
                    format!(
                        "uv finished, but {} is not there.",
                        runtime.python().display()
                    ),
                    "Press Try again; if it fails again, the log has what uv printed.",
                ));
            }
            recorded(PACKAGE_STEP)
        }
        MODELS_STEP => {
            let Some(choice) = choice else {
                return Err(Stop::plain(
                    "The models were not chosen.",
                    "Go back, choose the models and press Install.",
                ));
            };
            link(ctx, &runtime)?;
            run_step(
                ctx,
                shared,
                log,
                step,
                "dotx setup",
                &dotx_setup(&ctx.tools, &runtime, choice),
            )?;
            recorded(MODELS_STEP)
        }
        SERVICE_STEP => {
            link(ctx, &runtime)?;
            run_step(
                ctx,
                shared,
                log,
                step,
                "dotx service install",
                &dotx_service(&ctx.tools, &runtime),
            )?;
            recorded(SERVICE_STEP)
        }
        _ => {
            let deadline = Instant::now() + ctx.daemon_wait;
            while !answered() {
                if shared.cancel.load(Ordering::SeqCst) || Instant::now() >= deadline {
                    return Err(stop_of(
                        step,
                        "Dot's daemon",
                        Ran {
                            ended: Ended::TimedOut,
                            lines: Vec::new(),
                        },
                        ctx,
                    ));
                }
                thread::sleep(Duration::from_millis(500));
            }
            Ok(())
        }
    }
}

/// Run the steps from the first that is not done, until one fails or all are. Each step first asks `refusal` (but the
/// wait for the daemon, which is Dot's own by then), so that another Dot that appeared in the meantime is never installed
/// over.
fn run_steps(
    ctx: &Context,
    shared: &Shared,
    log: &Log,
    refusal: &dyn Fn() -> Option<Reason>,
    answered: &dyn Fn() -> bool,
) {
    // What the person chose, if they did in this session: only the models step needs it, and that step is done already
    // when an install that stopped after it is resumed.
    let choice = shared.with(|inner| inner.choice.clone());
    log.line(&format!("install of Dot {} begins", ctx.version));
    for (step, (_, title)) in STEPS.iter().enumerate() {
        if shared.with(|inner| inner.states[step] == StepState::Done) {
            continue;
        }
        if step != DAEMON_STEP {
            if let Some(reason) = refusal() {
                let stop = Stop::plain(
                    format!(
                        "The install stopped before \"{title}\", because {}.",
                        reason.words()
                    ),
                    "Dot is there already, so nothing was changed: close this window and open Dot.",
                );
                fail(shared, log, step, stop);
                return;
            }
        }
        shared.with(|inner| {
            inner.states[step] = StepState::Running;
            inner.output[step].clear();
        });
        log.line(&format!("== {title} =="));
        match do_step(ctx, shared, log, step, choice.as_ref(), answered) {
            Ok(()) => shared.with(|inner| inner.states[step] = StepState::Done),
            Err(stop) => {
                fail(shared, log, step, stop);
                return;
            }
        }
    }
    shared.with(|inner| {
        inner.phase = Phase::Done;
        inner.choice = inner.choice.take().map(|choice| Choice {
            key: None,
            ..choice
        });
    });
    log.line("install of Dot is done");
}

fn fail(shared: &Shared, log: &Log, step: usize, stop: Stop) {
    log.line(&format!("stopped at {}: {}", STEPS[step].0, stop.message));
    shared.with(|inner| {
        inner.states[step] = StepState::Failed;
        inner.phase = Phase::Failed;
        inner.failure = Some(Failure {
            step: STEPS[step].0,
            title: STEPS[step].1,
            message: stop.message,
            output: stop.output,
            fix: stop.fix,
        });
    });
}

impl Installer {
    pub fn new(context: Option<Context>) -> Installer {
        Installer {
            context: context.map(Arc::new),
            shared: Arc::new(Shared {
                inner: Mutex::new(Inner::new()),
                running: AtomicBool::new(false),
                cancel: AtomicBool::new(false),
            }),
        }
    }

    pub fn context(&self) -> Option<Arc<Context>> {
        self.context.clone()
    }

    /// Whether the install is under way.
    pub fn running(&self) -> bool {
        self.shared.running.load(Ordering::SeqCst)
    }

    /// End a step that runs, and wait a moment for it to end: the app is closing.
    pub fn stop(&self) {
        self.shared.cancel.store(true, Ordering::SeqCst);
        let deadline = Instant::now() + Duration::from_secs(3);
        while self.running() && Instant::now() < deadline {
            thread::sleep(TICK);
        }
    }

    pub fn status(&self) -> Status {
        let log = self.context.as_ref().map_or_else(String::new, |ctx| {
            ctx.log_dir.join(LOG_FILE).display().to_string()
        });
        self.shared.with(|inner| Status {
            phase: inner.phase,
            steps: STEPS
                .iter()
                .enumerate()
                .map(|(step, (id, title))| StepView {
                    id,
                    title,
                    state: inner.states[step],
                    output: inner.output[step].clone(),
                })
                .collect(),
            failure: inner.failure.clone(),
            can_change_models: inner.choice.is_some()
                || inner.states[MODELS_STEP] != StepState::Done,
            log,
        })
    }

    /// Begin, or begin again, from the first step that is not done: with the choice the page sent, or the one it sent
    /// before, or none when the models are done already. Refused when another Dot is here, when the install is complete
    /// and this session has no run that failed to go on with (a step of it that failed, the wait for the daemon among
    /// them, is tried again: the disk may show its work done already, a service the job of which is loaded), when the
    /// app is not where an app should be, or when a run is under way.
    fn begin(&self, choice: Option<Choice>) -> Result<(), String> {
        let Some(ctx) = self.context.clone() else {
            return Err("This build of Dot has no installer.".to_string());
        };
        let found = ctx.found();
        if let Some(reason) = foreign(&found) {
            return Err(format!("Dot is installed already: {}.", reason.words()));
        }
        let done = found.done();
        let phase = self.shared.with(|inner| inner.phase);
        if done.is_some_and(Steps::complete) && matches!(phase, Phase::Idle | Phase::Done) {
            return Err(format!(
                "Dot is installed already: {}.",
                Reason::InstallComplete.words()
            ));
        }
        if !ctx.app.as_deref().is_none_or(app_location_ok) {
            return Err(
                "Move Dot to the Applications folder and open it from there first.".to_string(),
            );
        }
        if self
            .shared
            .running
            .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
            .is_err()
        {
            return Err("The install is running already.".to_string());
        }
        let started = self.shared.with(|inner| -> Result<bool, String> {
            if inner.phase == Phase::Idle {
                // The first run of this session: what the marker and the disk show done is done, and the install goes
                // on from the first step that is not (never the models again, once they are set up).
                let done = done.unwrap_or_default();
                for (step, finished) in [
                    (PACKAGE_STEP, done.package),
                    (MODELS_STEP, done.setup),
                    (SERVICE_STEP, done.service),
                ] {
                    if finished {
                        inner.states[step] = StepState::Done;
                    }
                }
            }
            if let Some(choice) = choice {
                // The models were set up before this session, and the person chose none in it (the install was
                // resumed): the install goes on without them, which a choice cannot change. It is refused in words,
                // and not dropped without a word, with the key it may carry.
                if inner.choice.is_none() && inner.states[MODELS_STEP] == StepState::Done {
                    return Err(MODELS_KEPT.to_string());
                }
                // Another model or key than the one this session chose is another setup: from there on the steps are
                // done again.
                if inner
                    .choice
                    .as_ref()
                    .is_some_and(|before| *before != choice)
                {
                    for step in MODELS_STEP..STEPS.len() {
                        inner.states[step] = StepState::Waiting;
                    }
                }
                inner.choice = Some(choice);
            }
            if inner.choice.is_none() && inner.states[MODELS_STEP] != StepState::Done {
                return Ok(false);
            }
            for state in &mut inner.states {
                if *state == StepState::Failed {
                    *state = StepState::Waiting;
                }
            }
            inner.failure = None;
            inner.phase = Phase::Running;
            Ok(true)
        });
        let started = match started {
            Ok(started) => started,
            Err(words) => {
                self.shared.running.store(false, Ordering::SeqCst);
                return Err(words);
            }
        };
        if !started {
            self.shared.running.store(false, Ordering::SeqCst);
            return Err("There is nothing to try again: choose the models first.".to_string());
        }
        self.shared.cancel.store(false, Ordering::SeqCst);
        let shared = self.shared.clone();
        let spawned = thread::Builder::new()
            .name("dot-installer".to_string())
            .spawn(move || {
                let log = Log::open(&ctx.log_dir);
                let refusal = || foreign(&ctx.found());
                let answered = || (ctx.answers)(&ctx.home);
                run_steps(&ctx, &shared, &log, &refusal, &answered);
                shared.running.store(false, Ordering::SeqCst);
            });
        if let Err(error) = spawned {
            self.shared.running.store(false, Ordering::SeqCst);
            return Err(format!("The install could not be started: {error}"));
        }
        Ok(())
    }
}

/// Whether the app runs from a place it may stay in: not from the disk image it came on (which is ejected, and takes
/// the tools with it), and not from the randomized read-only place macOS runs a downloaded app from until it is moved.
pub fn app_location_ok(app: &Path) -> bool {
    let path = app.to_string_lossy();
    !path.starts_with("/Volumes/") && !path.contains("/AppTranslocation/")
}

/// The folder of `Dot.app` that holds `exe`, the app's program (`Dot.app/Contents/MacOS/<name>`).
pub fn app_of(exe: &Path) -> Option<PathBuf> {
    exe.ancestors()
        .nth(3)
        .filter(|app| app.extension().is_some_and(|ext| ext == "app"))
        .map(Path::to_path_buf)
}

/// The address of the daemon's health check, from where Dot's `home` says it listens.
fn health_url(home: &Path) -> Option<String> {
    crate::read_base(home)
        .ok()
        .map(|base| format!("{base}/api/health"))
}

/// `GET /api/health`.
#[derive(Deserialize)]
struct Health {
    ok: bool,
    version: String,
}

/// Whether a Dot daemon answers where Dot's `home` says it listens: its health check is answered as Dot's is. It asks
/// no token and sends none.
pub fn daemon_answers(home: &Path) -> bool {
    let Some(url) = health_url(home) else {
        return false;
    };
    ureq::get(&url)
        .config()
        .timeout_global(Some(REQUEST_TIMEOUT))
        .build()
        .call()
        .ok()
        .and_then(|response| response.into_body().read_json::<Health>().ok())
        .is_some_and(|health| health.ok && !health.version.is_empty())
}

#[derive(Clone, Debug, Serialize)]
pub struct Check {
    ok: bool,
    detail: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct Ollama {
    running: bool,
    models: Vec<String>,
}

/// What the Mac has: the page's first step.
#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct Inspection {
    version: String,
    macos: Check,
    chrome: Check,
    ollama: Ollama,
    location: Check,
    providers: Vec<Provider>,
    /// An install of the app's own stopped before the end: this one goes on from there.
    resuming: bool,
    /// The models are set up already (by this install's own `dotx setup`, or a `config.toml` that is there): the install
    /// goes on without asking for them again, and never runs `dotx setup` over them.
    models_done: bool,
    /// Another Dot is here: the installer will not run.
    blocked: Option<String>,
    log: String,
}

/// Whether `version` (`15.2`, `26.0.1`) is `minimum` or later.
fn at_least(version: &str, minimum: (u64, u64)) -> bool {
    let mut parts = version
        .trim()
        .split('.')
        .map(|part| part.parse::<u64>().ok());
    match (parts.next().flatten(), parts.next().flatten().or(Some(0))) {
        (Some(major), Some(minor)) => (major, minor) >= minimum,
        _ => false,
    }
}

fn macos_version() -> Option<String> {
    let out = Command::new(SW_VERS).arg("-productVersion").output().ok()?;
    out.status
        .success()
        .then(|| String::from_utf8_lossy(&out.stdout).trim().to_string())
        .filter(|version| !version.is_empty())
}

/// The models of the Ollama on this Mac, without the ones that only embed text, or `None` when nothing answers.
fn ollama_models() -> Option<Vec<String>> {
    #[derive(Deserialize)]
    struct Tags {
        models: Vec<Tag>,
    }
    #[derive(Deserialize)]
    struct Tag {
        name: String,
    }
    let tags: Tags = ureq::get(OLLAMA_TAGS)
        .config()
        .timeout_global(Some(REQUEST_TIMEOUT))
        .build()
        .call()
        .ok()?
        .into_body()
        .read_json()
        .ok()?;
    Some(
        tags.models
            .into_iter()
            .map(|tag| tag.name)
            .filter(|name| !name.contains("embed") && !name.starts_with("bge-"))
            .collect(),
    )
}

fn inspect(ctx: &Context) -> Inspection {
    let found = ctx.found();
    let version = macos_version();
    let ollama = ollama_models();
    Inspection {
        version: ctx.version.clone(),
        macos: Check {
            ok: version.as_deref().is_some_and(|v| at_least(v, MIN_MACOS)),
            detail: version.map_or_else(
                || "the version could not be read".to_string(),
                |version| format!("macOS {version}"),
            ),
        },
        chrome: Check {
            // Where the browser tools look for it (`CHROME_APP` of `dotagent.browser.login`), and nowhere else.
            ok: Path::new(CHROME_APP).exists(),
            detail: "the browser tools use it".to_string(),
        },
        ollama: Ollama {
            running: ollama.is_some(),
            models: ollama.unwrap_or_default(),
        },
        location: Check {
            ok: ctx.app.as_deref().is_none_or(app_location_ok),
            detail: ctx
                .app
                .as_ref()
                .map_or_else(String::new, |app| app.display().to_string()),
        },
        providers: providers(),
        resuming: resumes(&found),
        models_done: found.done().is_some_and(|steps| steps.setup),
        blocked: blocker(&found).map(|reason| reason.words().to_string()),
        log: ctx.log_dir.join(LOG_FILE).display().to_string(),
    }
}

#[derive(Clone, Copy, Debug, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Open {
    Chrome,
    Ollama,
    Log,
}

/// What `/usr/bin/open` is given for `what`: a fixed address, or the installer's log to show in Finder (`-R`).
fn open_args(what: Open, log: &Path) -> Vec<OsString> {
    match what {
        Open::Chrome => vec![CHROME_PAGE.into()],
        Open::Ollama => vec![OLLAMA_PAGE.into()],
        Open::Log => vec!["-R".into(), log.into()],
    }
}

/// What the page's window needs from the app: its tools, its version and where it is.
pub fn context_of(app: &AppHandle, home: PathBuf) -> Option<Context> {
    let resources = app.path().resource_dir().ok()?;
    let tools = bundled(&resources)?;
    let user_home = PathBuf::from(std::env::var_os("HOME")?);
    let log_dir = match std::env::var_os("DOT_LOG_DIR") {
        Some(dir) if !dir.is_empty() => PathBuf::from(dir),
        _ => user_home.join("Library").join("Logs").join("Dot"),
    };
    Some(Context {
        tools,
        home,
        user_home,
        version: app.package_info().version.to_string(),
        log_dir,
        app: std::env::current_exe().ok().as_deref().and_then(app_of),
        answers: daemon_answers,
        daemon_wait: DAEMON_WAIT,
    })
}

/// Whether the installer is what the window shows: this build has the tools and nothing of Dot is here.
pub fn wanted(ctx: &Context) -> bool {
    decide(&ctx.found()) == Decision::Installer
}

/// What the app does at every start with an install it made and finished (`Found::done`: its service is installed):
/// point the link at its tools where it is now, and when the package installed is older than the app, upgrade it and
/// have the daemon restart once no run works. It is done whether or not the daemon answers at that start: the daemon's
/// first answer decides nothing about the install. Nothing is done, and the log says so, while the app runs from a
/// place it does not stay in (a disk image, the copy macOS makes of a downloaded app: the links would point into it, and
/// into nothing once it is gone) and while the service is another Dot's (a job of a checkout that took its place: its
/// daemon is not the app's to restart). `Ok(None)` when there was nothing to do. A failure is the words for the person;
/// the package stays as it was.
pub fn keep_in_step(ctx: &Context) -> Result<Option<String>, String> {
    let runtime = ctx.runtime();
    let seen = found(&ctx.home, &ctx.user_home, false);
    let (Some(marker), Some(steps)) = (
        seen.marker.as_ref(),
        seen.done().filter(|steps| steps.complete()),
    ) else {
        return Ok(None);
    };
    let log = Log::open(&ctx.log_dir);
    if let Some(app) = ctx.app.as_deref().filter(|app| !app_location_ok(app)) {
        log.line(&format!(
            "the app runs from {}, which is not a place it stays in (a disk image, or the copy macOS makes of a downloaded app): the links to its tools, the package and the daemon are left as they are",
            app.display()
        ));
        return Ok(None);
    }
    if foreign(&seen).is_some() {
        log.line(
            "the launchd job of Dot's service is not the one this app installed (another Dot's, a checkout's, took its place): the package and the daemon are left as they are",
        );
        return Ok(None);
    }
    if marker.steps != Some(steps) {
        // A marker an older build wrote, or one that the disk adds to, says from now on what is true of the install:
        // a `config.toml` that is deleted later does not make it unfinished. The disk is read again at every start
        // until this is written, so a failure is said and the start goes on.
        if let Err(error) = write_marker(
            &runtime,
            &Marker {
                version: marker.version.clone(),
                steps: Some(steps),
            },
        ) {
            let words = format!(
                "The install's marker could not be written: {error}. What it says is read from the disk at every start until it can be."
            );
            eprintln!("dot-shell: {words}");
            log.line(&words);
        }
    }
    link_tools(&runtime, &ctx.tools)
        .map_err(|error| format!("The link to the app's tools could not be made: {error}"))?;
    let Some(installed) = installed_version(&runtime) else {
        return Ok(None);
    };
    if !older(&installed, &ctx.version) {
        return Ok(None);
    }
    log.line(&format!(
        "the package is {installed} and the app is {}: upgrading",
        ctx.version
    ));
    let never = AtomicBool::new(false);
    let attempt = |what: &str, plan: &Plan| -> Result<(), String> {
        let ran = run(plan, &runtime.dir, &log, &never, |_| {});
        match ran.ended {
            Ended::Exited(0) => Ok(()),
            ended => {
                let last = ran.lines.last().cloned().unwrap_or_default();
                Err(format!("{what} did not end well ({ended:?}): {last}"))
            }
        }
    };
    attempt(
        "uv tool install",
        &uv_install(&ctx.tools, &runtime, &ctx.version, true),
    )
    .map_err(|error| {
        format!(
            "Dot's engine could not be updated to {}: {error}. Its log is {}.",
            ctx.version,
            log.path.display()
        )
    })?;
    write_marker(
        &runtime,
        &Marker {
            version: ctx.version.clone(),
            steps: Some(steps),
        },
    )
    .map_err(|error| format!("The install's marker could not be written: {error}"))?;
    match attempt(
        "dotx service restart --when-idle",
        &dotx_restart(&ctx.tools, &runtime),
    ) {
        Ok(()) => Ok(Some(format!(
            "Dot's engine is {} now. Dot restarts once no run works.",
            ctx.version
        ))),
        Err(error) => Err(format!(
            "Dot's engine is {} now, but the daemon was not asked to restart: {error}. It uses the new engine from its next start. The log is {}.",
            ctx.version,
            log.path.display()
        )),
    }
}

/// Whether the installer may run, for the page's commands: the context is there.
fn ready(installer: &State<'_, Installer>) -> Result<Arc<Context>, String> {
    installer
        .context()
        .ok_or_else(|| "This build of Dot has no installer.".to_string())
}

#[tauri::command]
pub async fn installer_inspect(installer: State<'_, Installer>) -> Result<Inspection, String> {
    let ctx = ready(&installer)?;
    tauri::async_runtime::spawn_blocking(move || inspect(&ctx))
        .await
        .map_err(|error| error.to_string())
}

#[tauri::command]
pub fn installer_status(installer: State<'_, Installer>) -> Status {
    installer.status()
}

#[tauri::command]
pub async fn installer_start(
    installer: State<'_, Installer>,
    choice: ChoiceInput,
) -> Result<(), String> {
    let choice = Choice::check(choice, &providers())?;
    let installer = installer.inner().clone();
    tauri::async_runtime::spawn_blocking(move || installer.begin(Some(choice)))
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command]
pub async fn installer_retry(installer: State<'_, Installer>) -> Result<(), String> {
    let installer = installer.inner().clone();
    tauri::async_runtime::spawn_blocking(move || installer.begin(None))
        .await
        .map_err(|error| error.to_string())?
}

#[tauri::command]
pub async fn installer_open(installer: State<'_, Installer>, what: Open) -> Result<(), String> {
    let ctx = ready(&installer)?;
    let args = open_args(what, &ctx.log_dir.join(LOG_FILE));
    tauri::async_runtime::spawn_blocking(move || {
        Command::new(OPEN)
            .args(&args)
            .status()
            .map_err(|error| format!("open could not be started: {error}"))
            .and_then(|status| {
                status
                    .success()
                    .then_some(())
                    .ok_or_else(|| format!("open could not show it: {status}"))
            })
    })
    .await
    .map_err(|error| error.to_string())?
}

/// Whether `page` is the installer's own page, the one address its window may show.
pub fn is_installer_page(page: &url::Url) -> bool {
    page.scheme() == "tauri"
        && page.host_str() == Some("localhost")
        && page.path() == format!("/{PAGE}")
}

#[cfg(test)]
mod tests;
