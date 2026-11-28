//! The desktop shell of Dot: a native window around the web UI the daemon serves, a menu bar item with the face of
//! the web UI and the count of what waits for the user, a global shortcut for a new chat, and notifications sent by
//! the web UI through the shell. The daemon stays owned by launchd (`dotx service install`): the shell connects to it
//! with the token of the token file and never runs the agent itself. It sends the token only to a daemon that has
//! just proven it holds it (`GET /api/proof`), so a program that took the daemon's port is sent nothing. The pages of
//! the daemon's own address are the only remote pages that may use the shell's commands (`grant`). While the daemon is
//! down the window shows the page that says how to start it, or which of the daemon's files the shell could not read
//! and why (`Unread`), and opens the UI by itself when the daemon answers. On a Mac that has no Dot at all, a window of
//! its own shows the installer instead (`installer`, item 575): the only page that may call the install commands.
//! `[desktop_app]` of the daemon's config.toml, read at each poll, says whether the shell has a login item
//! (`login_item`) and the language of its own words (`words`); the window keeps its place across launches
//! (`window_place`), and the releases are looked at once a day while the shell runs.

mod installer;
mod login_item;
mod window_place;
mod words;

use std::collections::HashSet;
use std::env;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, SystemTime};

use hmac::{Hmac, KeyInit, Mac};
use rfd::{AsyncMessageDialog, MessageButtons, MessageDialogResult, MessageLevel};
use serde::Deserialize;
use sha2::Sha256;
use tauri::image::Image;
use tauri::ipc::CapabilityBuilder;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent};
use tauri::webview::{NewWindowResponse, PermissionKind, PermissionResponse};
use tauri::{
    AppHandle, LogicalPosition, Manager, RunEvent, TitleBarStyle, WebviewUrl, WebviewWindow,
    WebviewWindowBuilder, WindowEvent,
};
use tauri::{LogicalSize, Wry};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};
use tauri_plugin_updater::{Update, UpdaterExt};
use window_place::{Area, Place};
use words::{fill, Lang, Words};

const WINDOW: &str = "main";
const TRAY: &str = "main";
const DOWN_PAGE: &str = "down.html";
const POLL: Duration = Duration::from_secs(1);
const REQUEST_TIMEOUT: Duration = Duration::from_millis(800);
/// Polls in a row the daemon may leave unanswered before the window leaves the UI for the down page (item 200): a
/// restart of the daemon, one to six seconds from its stop to its start, and one slow answer of a busy daemon leave the
/// open page as it is, which says that it reconnects and reads again what changed once the daemon is back.
const MISSES_BEFORE_DOWN: u32 = 10;
const DEFAULT_HOST: &str = "127.0.0.1";
const DEFAULT_PORT: u16 = 8765;
const CONFIG_FILE: &str = "config.toml";
const TOKEN_FILE: &str = "api-token";
/// How often the releases are looked at while the shell runs (and once as it starts), by the wall clock, which goes on
/// while the Mac sleeps.
const UPDATE_EVERY: Duration = Duration::from_secs(24 * 60 * 60);
/// The window's size when it has no place kept yet (item 88), and the least it may be.
const WINDOW_SIZE: (f64, f64) = (1280.0, 860.0);
const WINDOW_MIN: (f64, f64) = (390.0, 600.0);
const PLACE_FILE: &str = "window.json";
/// What the daemon's proof puts before the nonce (`daemon_proof` in `dotagent.server.guard`).
const PROOF_LABEL: &[u8] = b"dot daemon proof\n";
const NONCE_BYTES: usize = 32;
/// What a page of the window may do through the shell: drag the window by its bars and zoom it with a double click
/// there (the drag script Tauri adds to a page asks for both), and notifications. Nothing else of Tauri's core is
/// granted (item 561): `core:default` would let a page read any PNG from disk (`image`), change the menu bar item and
/// the app menu (`tray`, `menu`) and emit events. `capabilities/default.json` gives the same to the shell's own pages.
const PAGE_PERMISSIONS: [&str; 3] = [
    "core:window:allow-start-dragging",
    "core:window:allow-internal-toggle-maximize",
    "notification:default",
];
const ICON_IDLE: &[u8] = include_bytes!("../icons/tray-idle.png");
const ICON_WORKING: &[u8] = include_bytes!("../icons/tray-working.png");
const ICON_WAITING: &[u8] = include_bytes!("../icons/tray-waiting.png");
/// Where the window puts its traffic lights (item 134): the close button 20 px from the left and its centre 26 px under
/// the top, the centre line of the web UI's top bars (52 px rows; shell.css keeps 96 px of room for the lights). On
/// macOS 26 the close button's centre is the inset's y less 2 px, as measured offscreen with tao's own code.
const TRAFFIC_LIGHTS: LogicalPosition<f64> = LogicalPosition::new(20.0, 28.0);

/// `[server]` of the daemon's config.toml; what is not set keeps the daemon's default.
#[derive(Deserialize, Default)]
struct ServerSection {
    host: Option<String>,
    port: Option<u16>,
}

/// `[desktop_app]` of the daemon's config.toml (`DesktopAppConfig` in `dotagent.config`); what is not set keeps the
/// daemon's default.
#[derive(Deserialize, Default)]
struct DesktopAppSection {
    start_at_login: Option<bool>,
    language: Option<String>,
}

#[derive(Deserialize, Default)]
struct ConfigFile {
    server: Option<ServerSection>,
    desktop_app: Option<DesktopAppSection>,
}

/// What the shell does as `[desktop_app]` says: whether it has a login item, and the language of its words.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct DesktopApp {
    start_at_login: bool,
    lang: Lang,
}

impl DesktopApp {
    /// The settings `config` gives, the daemon's defaults (`defaults.toml`) for what it leaves out.
    fn of(config: &ConfigFile) -> DesktopApp {
        let section = config.desktop_app.as_ref();
        DesktopApp {
            start_at_login: section.and_then(|app| app.start_at_login).unwrap_or(true),
            lang: section
                .and_then(|app| app.language.as_deref())
                .map_or(Lang::En, Lang::of),
        }
    }
}

/// `GET /api/activity/count`.
#[derive(Deserialize)]
struct Count {
    waiting: u64,
    working: u64,
    state: String,
}

/// `GET /api/proof`.
#[derive(Deserialize)]
struct Proof {
    proof: String,
}

type HmacSha256 = Hmac<Sha256>;

/// The daemon addresses whose pages were given the shell's commands (`grant`).
#[derive(Default)]
struct Granted(Mutex<HashSet<String>>);

/// What a poll sees at the daemon's address.
enum Seen {
    /// The daemon: it proved that it holds the token, then gave the numbers of the menu bar item.
    Counted(Count),
    /// Nothing answers in time.
    Silent,
    /// Something answers at this address without the proof the token gives (another program on the port, a daemon of
    /// an older version or of another home): it is sent nothing.
    Unproven(String),
    /// The shell could not read where the daemon listens or its token, so it asked nothing.
    Unread(Unread),
}

/// What kept the shell from reading the daemon's files (item 191): the menu bar item's tip and the down page say it,
/// instead of "Dot is not running".
#[derive(Debug, PartialEq, Eq)]
enum Unread {
    /// config.toml could not be read, or is not valid TOML, or is not the shape of a config: the system's or the
    /// parser's words, with the line and column of what the parser found.
    Config { path: PathBuf, error: String },
    /// The token file is there but could not be read.
    Token { path: PathBuf, error: String },
    /// The token file is there but empty, which the daemon refuses too.
    EmptyToken { path: PathBuf },
    /// There is no token file: the daemon makes it the first time it starts with this home.
    NoToken { path: PathBuf },
}

impl Unread {
    /// What the menu bar item's tip says.
    fn tip(&self, words: &Words) -> &'static str {
        match self {
            Unread::Config { .. } => words.tip_config,
            Unread::Token { .. } | Unread::EmptyToken { .. } => words.tip_token,
            Unread::NoToken { .. } => words.tip_no_token,
        }
    }

    /// What the down page says instead of "Dot is not running" and the advice to start it: with a file the shell could
    /// not read the daemon may well run. A home without a token is one where the daemon has never started.
    fn title(&self, words: &Words) -> Option<&'static str> {
        match self {
            Unread::Config { .. } => Some(words.title_config),
            Unread::Token { .. } | Unread::EmptyToken { .. } => Some(words.title_token),
            Unread::NoToken { .. } => None,
        }
    }

    /// The file and what is wrong with it, as the down page shows it: the system's or the parser's words as they are.
    fn detail(&self, words: &Words) -> String {
        match self {
            Unread::Config { path, error } | Unread::Token { path, error } => {
                format!("{}: {error}", path.display())
            }
            Unread::EmptyToken { path } => {
                format!("{}: {}", path.display(), words.detail_empty_token)
            }
            Unread::NoToken { path } => format!("{}: {}", path.display(), words.detail_no_token),
        }
    }
}

/// Where the daemon listens and the token that signs the shell in.
#[derive(Clone)]
struct Daemon {
    base: String,
    token: String,
}

/// The page the window shows (`watch`): the UI, or the down page at the address that says why (`down_url`), or, on a
/// Mac that has no Dot, the installer's own window (`installer`).
enum Shown {
    /// The UI at the address it was opened at (`ui_url`): a daemon that comes back at another port or with another
    /// token is opened anew (the review of 2026-11-01), not left on a page that cannot reach it.
    Ui(String),
    Down(String),
    Installer,
}

/// The daemon's home: `DOT_HOME`, else `~/Library/Application Support/Dot` (`dotagent.core.paths`).
fn dot_home() -> PathBuf {
    match env::var("DOT_HOME") {
        Ok(path) if !path.is_empty() => PathBuf::from(path),
        _ => {
            let home = env::var("HOME").unwrap_or_else(|_| "/".to_string());
            PathBuf::from(home)
                .join("Library")
                .join("Application Support")
                .join("Dot")
        }
    }
}

/// The text of the file at `path`, `None` when there is none, else the system's words for why it could not be read.
fn read_file(path: &Path) -> Result<Option<String>, String> {
    match fs::read_to_string(path) {
        Ok(text) => Ok(Some(text)),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error.to_string()),
    }
}

/// What the TOML parser found wrong in `text`, after its line and column as an editor counts them when it says where.
fn toml_error(text: &str, error: &toml::de::Error) -> String {
    let what = error
        .message()
        .lines()
        .map(str::trim)
        .filter(|line| !line.is_empty())
        .collect::<Vec<_>>()
        .join("; ");
    match error.span().and_then(|span| text.get(..span.start)) {
        Some(before) => {
            let line = before.matches('\n').count() + 1;
            let column = before
                .rsplit('\n')
                .next()
                .unwrap_or_default()
                .chars()
                .count()
                + 1;
            format!("line {line}, column {column}: {what}")
        }
        None => what,
    }
}

/// Where the daemon of `home` listens, read as the daemon reads it: without a config.toml it keeps its defaults. What
/// the shell could not read is told (`Unread`), and no address is guessed in its place. The installer asks it too
/// (item 575), before there is a token.
fn read_base(home: &Path) -> Result<String, Unread> {
    read_config(home).map(|config| base_of(&config))
}

/// The daemon's config.toml as the shell reads it; without one, the daemon's defaults.
fn read_config(home: &Path) -> Result<ConfigFile, Unread> {
    let config_path = home.join(CONFIG_FILE);
    let config: ConfigFile = match read_file(&config_path) {
        Ok(None) => ConfigFile::default(),
        Ok(Some(text)) => toml::from_str(&text).map_err(|error| Unread::Config {
            path: config_path.clone(),
            error: toml_error(&text, &error),
        })?,
        Err(error) => {
            return Err(Unread::Config {
                path: config_path,
                error,
            })
        }
    };
    Ok(config)
}

/// Where the daemon `config` describes listens.
fn base_of(config: &ConfigFile) -> String {
    let server = config.server.as_ref();
    let host = server
        .and_then(|server| server.host.clone())
        .unwrap_or_else(|| DEFAULT_HOST.to_string());
    let port = server
        .and_then(|server| server.port)
        .unwrap_or(DEFAULT_PORT);
    // An IPv6 host goes in brackets, as `dotagent.server.links.base_url` writes it.
    let bracketed = if host.contains(':') {
        format!("[{host}]")
    } else {
        host
    };
    format!("http://{bracketed}:{port}")
}

/// Where the daemon of `home` listens and its token (`read_base`, `read_token`).
#[cfg(test)]
fn read_daemon(home: &Path) -> Result<Daemon, Unread> {
    let base = read_base(home)?;
    Ok(Daemon {
        base,
        token: read_token(home)?,
    })
}

/// The token of the daemon of `home`: the token file must be there and not empty.
fn read_token(home: &Path) -> Result<String, Unread> {
    let token_path = home.join(TOKEN_FILE);
    let token = match read_file(&token_path) {
        Ok(None) => return Err(Unread::NoToken { path: token_path }),
        Ok(Some(text)) => text.trim().to_string(),
        Err(error) => {
            return Err(Unread::Token {
                path: token_path,
                error,
            })
        }
    };
    if token.is_empty() {
        return Err(Unread::EmptyToken { path: token_path });
    }
    Ok(token)
}

/// What the pages of the daemon's own address may do through the shell (`PAGE_PERMISSIONS`). A page that another
/// program serves on another port of this Mac, or under another name of it, may do nothing.
fn daemon_capability(daemon: &Daemon) -> CapabilityBuilder {
    PAGE_PERMISSIONS.iter().fold(
        CapabilityBuilder::new("daemon")
            .remote(daemon.base.clone())
            .local(false)
            .window(WINDOW),
        |capability, permission| capability.permission(*permission),
    )
}

/// Give the pages of the daemon's address the shell's commands, once for each address: the one config.toml names when
/// the shell starts, and another it names later, when the daemon moved.
fn grant(app: &AppHandle, daemon: &Daemon) -> tauri::Result<()> {
    let granted = app.state::<Granted>();
    let mut addresses = granted
        .0
        .lock()
        .expect("nothing panics while it holds the granted addresses");
    if !addresses.contains(&daemon.base) {
        app.add_capability(daemon_capability(daemon))?;
        addresses.insert(daemon.base.clone());
    }
    Ok(())
}

/// The address that opens the UI signed in: the token travels in the fragment, which is never sent.
fn ui_url(daemon: &Daemon) -> String {
    format!("{}/#token={}", daemon.base, daemon.token)
}

/// What the down page says of something that answers at `address` without the proof (`Seen::Unproven`): the shell
/// sent it nothing, so it is not this home's daemon, and the way to see what it is, as `dotx service` gives it.
fn unproven_detail(address: &str, words: &Words) -> String {
    let listening = match url::Url::parse(address)
        .ok()
        .and_then(|parsed| parsed.port())
    {
        Some(port) => format!("lsof -nP -iTCP:{port} -sTCP:LISTEN"),
        None => "lsof -nP -iTCP -sTCP:LISTEN".to_string(),
    };
    fill(words.detail_unproven, &[("listening", &listening)])
}

/// The down page, told in its fragment what kept the shell from the daemon (`down.js`): `title` stands for "Dot is
/// not running" and the advice to start it, `detail` says which file and what is wrong with it, or what answers at
/// the daemon's address instead of it, and `lang` the language of its own words when it is not English. A daemon that
/// does not answer, in English, gets the page as it is.
fn down_url(seen: &Seen, lang: Lang) -> String {
    let page = format!("tauri://localhost/{DOWN_PAGE}");
    let words = lang.words();
    let mut told = url::form_urlencoded::Serializer::new(String::new());
    if lang == Lang::Tr {
        told.append_pair("lang", "tr");
    }
    match seen {
        Seen::Unread(unread) => {
            if let Some(title) = unread.title(words) {
                told.append_pair("title", title);
            }
            told.append_pair("detail", &unread.detail(words));
        }
        Seen::Unproven(address) => {
            told.append_pair(
                "title",
                &fill(words.title_unproven, &[("address", address)]),
            );
            told.append_pair("detail", &unproven_detail(address, words));
        }
        Seen::Counted(_) | Seen::Silent => {}
    }
    let fragment = told.finish();
    if fragment.is_empty() {
        page
    } else {
        format!("{page}#{fragment}")
    }
}

/// Whether `answer` is the proof of `nonce` that only a holder of `token` gives: HMAC-SHA256 keyed with the token over
/// `PROOF_LABEL` and the nonce, in hex.
fn proves(token: &str, nonce: &str, answer: &str) -> bool {
    let Ok(tag) = hex::decode(answer) else {
        return false;
    };
    let mut mac =
        HmacSha256::new_from_slice(token.as_bytes()).expect("HMAC takes a key of any length");
    mac.update(PROOF_LABEL);
    mac.update(nonce.as_bytes());
    mac.verify_slice(&tag).is_ok()
}

/// Ask what answers at the daemon's address to prove that it holds `token`, with a fresh nonce: `None` when nothing
/// answers in time, else whether the answer is the proof.
fn proven(daemon: &Daemon, token: &str) -> Option<bool> {
    let mut bytes = [0u8; NONCE_BYTES];
    getrandom::fill(&mut bytes).expect("macOS gives random bytes");
    let nonce = hex::encode(bytes);
    match ureq::get(&format!("{}/api/proof", daemon.base))
        .query("nonce", &nonce)
        .config()
        .timeout_global(Some(REQUEST_TIMEOUT))
        .build()
        .call()
    {
        Ok(response) => Some(
            response
                .into_body()
                .read_json::<Proof>()
                .is_ok_and(|answer| proves(token, &nonce, &answer.proof)),
        ),
        Err(ureq::Error::StatusCode(_)) => Some(false),
        Err(_) => None,
    }
}

/// The numbers of the menu bar item, or `None` while the daemon does not answer.
fn count(daemon: &Daemon, token: &str) -> Option<Count> {
    ureq::get(&format!("{}/api/activity/count", daemon.base))
        .header("Authorization", &format!("Bearer {token}"))
        .config()
        .timeout_global(Some(REQUEST_TIMEOUT))
        .build()
        .call()
        .ok()?
        .into_body()
        .read_json()
        .ok()
}

/// One look at the daemon: the count is asked, with the token, only of a daemon that has just proven it holds it.
fn look(daemon: &Daemon) -> Seen {
    match proven(daemon, &daemon.token) {
        None => Seen::Silent,
        Some(false) => Seen::Unproven(daemon.base.clone()),
        Some(true) => count(daemon, &daemon.token).map_or(Seen::Silent, Seen::Counted),
    }
}

/// What one poll found.
struct Polled {
    seen: Seen,
    /// The daemon it looked at, when the shell could read where it listens and its token.
    daemon: Option<Daemon>,
    /// `[desktop_app]`, when the shell could read config.toml.
    app: Option<DesktopApp>,
}

/// One poll: what the shell reads of the daemon's `home`, then, when it could read it, what answers at its address.
fn poll(home: &Path) -> Polled {
    let config = match read_config(home) {
        Ok(config) => config,
        Err(unread) => {
            return Polled {
                seen: Seen::Unread(unread),
                daemon: None,
                app: None,
            }
        }
    };
    let app = Some(DesktopApp::of(&config));
    match read_token(home) {
        Ok(token) => {
            let daemon = Daemon {
                base: base_of(&config),
                token,
            };
            Polled {
                seen: look(&daemon),
                daemon: Some(daemon),
                app,
            }
        }
        Err(unread) => Polled {
            seen: Seen::Unread(unread),
            daemon: None,
            app,
        },
    }
}

/// The face of the menu bar item: Dot at rest, at work, or with something that waits for you.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Face {
    Idle,
    Working,
    Waiting,
}

impl Face {
    /// The face of the state `GET /api/activity/count` names; one the shell does not know is the face at rest.
    fn of(state: &str) -> Face {
        match state {
            "waiting" => Face::Waiting,
            "working" => Face::Working,
            _ => Face::Idle,
        }
    }
}

fn tray_icon(face: Face) -> Image<'static> {
    let bytes = match face {
        Face::Waiting => ICON_WAITING,
        Face::Working => ICON_WORKING,
        Face::Idle => ICON_IDLE,
    };
    Image::from_bytes(bytes)
        .expect("the tray icons are PNG files rendered by desktop/icons/render.py")
}

/// What the menu bar item shows: its face, the count of what waits for you beside it, and its tip.
#[derive(Debug, Clone, PartialEq, Eq)]
struct TrayLook {
    face: Face,
    title: Option<String>,
    tip: String,
}

/// How far the download of an update is (`offer_update`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
struct Progress {
    received: u64,
    /// The update's size, when the release says it.
    total: Option<u64>,
    /// The download is over and the update is being put in place.
    installing: bool,
}

impl Progress {
    /// How far it is in a few characters: the share of the size, else the megabytes so far.
    fn short(&self) -> String {
        match self.total {
            Some(total) if total > 0 => {
                format!("{}%", (self.received.saturating_mul(100) / total).min(100))
            }
            _ => format!("{:.1} MB", self.received as f64 / 1_000_000.0),
        }
    }
}

/// The download under way, which the menu bar item shows at each poll; `None` when there is none.
#[derive(Default)]
struct Downloading(Mutex<Option<Progress>>);

/// The language of the shell's words, as the last poll that read config.toml found it.
struct Speech(Mutex<Lang>);

/// Whether ⇧⌘Space could not be had, which the menu says in each language.
struct ShortcutTaken(bool);

/// The place of the window last saved (`window_place`), so that only a change is written.
#[derive(Default)]
struct SavedPlace(Mutex<Option<Place>>);

/// The language of the shell's words now.
fn lang_of(app: &AppHandle) -> Lang {
    *app.state::<Speech>()
        .0
        .lock()
        .expect("nothing panics while it holds the language")
}

/// The download under way now.
fn download_of(app: &AppHandle) -> Option<Progress> {
    *app.state::<Downloading>()
        .0
        .lock()
        .expect("nothing panics while it holds the download")
}

fn set_download(app: &AppHandle, progress: Option<Progress>) {
    *app.state::<Downloading>()
        .0
        .lock()
        .expect("nothing panics while it holds the download") = progress;
}

/// What the menu bar item shows after a poll that saw `seen`, while the installer's window is up or not and while an
/// update downloads (`download`): then the count beside the item says how far it is too, and the tip says only that.
fn tray_look(seen: &Seen, setting_up: bool, download: Option<Progress>, words: &Words) -> TrayLook {
    let (face, waiting, working) = match seen {
        Seen::Counted(count) => (Face::of(&count.state), count.waiting, count.working),
        Seen::Silent | Seen::Unproven(_) | Seen::Unread(_) => (Face::Idle, 0, 0),
    };
    let tip = match seen {
        _ if download.is_some_and(|progress| progress.installing) => {
            words.tip_installing.to_string()
        }
        _ if download.is_some() => fill(
            words.tip_downloading,
            &[(
                "progress",
                &download
                    .map(|progress| progress.short())
                    .unwrap_or_default(),
            )],
        ),
        _ if setting_up => words.tip_setting_up.to_string(),
        Seen::Counted(_) => fill(
            words.tip_counted,
            &[
                ("waiting", &waiting.to_string()),
                ("working", &working.to_string()),
            ],
        ),
        Seen::Silent => words.tip_silent.to_string(),
        Seen::Unproven(_) => words.tip_unproven.to_string(),
        Seen::Unread(unread) => unread.tip(words).to_string(),
    };
    let count = (waiting > 0).then(|| waiting.to_string());
    let title = match (count, download) {
        (Some(count), Some(progress)) => Some(format!("{count} · ↓ {}", progress.short())),
        (None, Some(progress)) => Some(format!("↓ {}", progress.short())),
        (count, None) => count,
    };
    TrayLook { face, title, tip }
}

/// Show `look` on the menu bar item; whether every part of it was set.
fn show_look(tray: &TrayIcon, look: &TrayLook) -> bool {
    let icon = tray.set_icon_with_as_template(Some(tray_icon(look.face)), false);
    let title = tray.set_title(look.title.as_deref());
    let tip = tray.set_tooltip(Some(look.tip.as_str()));
    icon.is_ok() && title.is_ok() && tip.is_ok()
}

/// The one window, made on first use, at the size the web UI's screenshot tour reviews (item 88). The title bar is
/// the web UI's own top bars: the system draws only the traffic lights over the page, on the bars' centre line
/// (`TRAFFIC_LIGHTS`), the page keeps room for them (`data-shell="desktop"`, set by the UI when it runs in the shell)
/// and drags the window by its bars. The shell's own drag-and-drop handler is off so the composer's HTML drop works.
/// A link the page opens in a new window (every link of a model's answer) goes to the default browser. What a page asks
/// of the web view itself, the microphone among it, is `permission_for`'s to answer. It opens where it was last
/// (`window_place`), when a display still shows that place, and hidden when `visible` is not set (the shell opened at
/// login).
fn window(app: &AppHandle, url: WebviewUrl, visible: bool) -> tauri::Result<WebviewWindow> {
    if let Some(existing) = app.get_webview_window(WINDOW) {
        return Ok(existing);
    }
    let saved = place_path(app).and_then(|path| window_place::read(&path));
    *app.state::<SavedPlace>()
        .0
        .lock()
        .expect("nothing panics while it holds the window's place") = saved;
    // Without the displays the place cannot be checked, so the window opens at its default size, as before item 2095.
    let monitors = app.available_monitors().unwrap_or_else(|error| {
        eprintln!("dot-shell: the displays could not be listed, so the window opens at its default place: {error}");
        Vec::new()
    });
    let displays: Vec<Area> = monitors
        .iter()
        .map(|monitor| {
            let scale = monitor.scale_factor();
            let position = monitor.position().to_logical::<f64>(scale);
            let size = monitor.size().to_logical::<f64>(scale);
            Area {
                x: position.x,
                y: position.y,
                width: size.width,
                height: size.height,
            }
        })
        .collect();
    let place = saved.and_then(|saved| window_place::usable(saved, &displays, WINDOW_MIN));
    let builder = WebviewWindowBuilder::new(app, WINDOW, url)
        .title("Dot")
        .title_bar_style(TitleBarStyle::Overlay)
        .hidden_title(true)
        .traffic_light_position(TRAFFIC_LIGHTS)
        .min_inner_size(WINDOW_MIN.0, WINDOW_MIN.1)
        .visible(visible);
    let builder = match place {
        Some(place) => builder
            .inner_size(place.width, place.height)
            .position(place.x, place.y)
            .maximized(place.maximized),
        None => builder.inner_size(WINDOW_SIZE.0, WINDOW_SIZE.1),
    };
    builder
        .disable_drag_drop_handler()
        .on_new_window(|url, _features| {
            open_outside(url);
            NewWindowResponse::Deny
        })
        .on_permission_request(|webview, kind| {
            let page = webview.url().ok();
            let granted = webview.app_handle().state::<Granted>();
            let addresses = granted
                .0
                .lock()
                .expect("nothing panics while it holds the granted addresses");
            permission_for(page.as_ref(), kind, &addresses)
        })
        .build()
}

/// Where the window's place is kept: the shell's own folder (`~/Library/Application Support/dev.dot.desktop`).
fn place_path(app: &AppHandle) -> Option<PathBuf> {
    match app.path().app_config_dir() {
        Ok(folder) => Some(folder.join(PLACE_FILE)),
        Err(error) => {
            eprintln!("dot-shell: the shell's own folder is not known, so the window keeps no place: {error}");
            None
        }
    }
}

/// Save where the window is and how big, when that changed since it was last saved. A minimized window or one in full
/// screen keeps the place it had.
fn save_place(window: &WebviewWindow) {
    let app = window.app_handle();
    let Some(path) = place_path(app) else {
        return;
    };
    let frame = (|| -> tauri::Result<Option<(Place, bool)>> {
        if window.is_minimized()? || window.is_fullscreen()? {
            return Ok(None);
        }
        let scale = window.scale_factor()?;
        let position = window.outer_position()?.to_logical::<f64>(scale);
        let size: LogicalSize<f64> = window.inner_size()?.to_logical(scale);
        let frame = Place {
            x: position.x,
            y: position.y,
            width: size.width,
            height: size.height,
            maximized: false,
        };
        Ok(Some((frame, window.is_maximized()?)))
    })();
    let (frame, maximized) = match frame {
        Ok(Some(frame)) => frame,
        Ok(None) => return,
        Err(error) => {
            eprintln!("dot-shell: the window's place could not be read: {error}");
            return;
        }
    };
    let saved = app.state::<SavedPlace>();
    let mut last = saved
        .0
        .lock()
        .expect("nothing panics while it holds the window's place");
    let place = window_place::to_save(frame, maximized, *last);
    if *last == Some(place) {
        return;
    }
    match window_place::write(&path, &place) {
        Ok(()) => *last = Some(place),
        Err(error) => eprintln!(
            "dot-shell: the window's place could not be saved to {}: {error}",
            path.display()
        ),
    }
}

/// What a page of the window is given of what it asks of the web view (item 594): the microphone, for the composer's
/// dictation (item 574), on a page of an address the shell gave its commands (`Granted`: the daemon's own, once it
/// proved itself); nothing else, and nothing to another page. macOS gives the microphone to the app, not to a page: the
/// web view would hand it to any page it showed, one a link led the window to among them, and wry grants every request
/// when no one answers it.
fn permission_for(
    page: Option<&url::Url>,
    kind: PermissionKind,
    granted: &HashSet<String>,
) -> PermissionResponse {
    // Both sides read as the `url` crate reads an origin: a granted address is written by hand (`read_daemon`), where a
    // name in capitals or the port of its scheme would not match the page's own spelling of it.
    let ours = page.is_some_and(|page| {
        granted
            .iter()
            .any(|base| url::Url::parse(base).is_ok_and(|base| base.origin() == page.origin()))
    });
    if ours && matches!(kind, PermissionKind::Microphone) {
        PermissionResponse::Allow
    } else {
        PermissionResponse::Deny
    }
}

/// Open a web address in the default browser; the shell opens no window of its own for it. Another kind of address
/// is refused. The address is not logged: a link can carry what its page put in it.
fn open_outside(url: url::Url) {
    if !matches!(url.scheme(), "http" | "https") {
        eprintln!(
            "dot-shell: a new window for a {} address was refused",
            url.scheme()
        );
        return;
    }
    thread::spawn(
        move || match Command::new("/usr/bin/open").arg(url.as_str()).status() {
            Ok(status) if status.success() => {}
            Ok(status) => eprintln!("dot-shell: open could not show a link: {status}"),
            Err(error) => eprintln!("dot-shell: open could not be started for a link: {error}"),
        },
    );
}

/// The installer's window, made on a Mac that has no Dot (item 575). Its page is the app's own `installer.html` and
/// nothing else: the window refuses every other address and every new window, so that no link or script can take it
/// to a page that is not bundled. Only this label has the install commands (`capabilities/installer.json`). Its page
/// asks the web view for nothing, and is given nothing it asks (the microphone, the camera, where the Mac is): wry
/// grants every request no one answers (item 594).
fn installer_window(app: &AppHandle) -> tauri::Result<WebviewWindow> {
    if let Some(existing) = app.get_webview_window(installer::WINDOW) {
        return Ok(existing);
    }
    WebviewWindowBuilder::new(
        app,
        installer::WINDOW,
        WebviewUrl::App(installer::PAGE.into()),
    )
    .title("Set up Dot")
    .title_bar_style(TitleBarStyle::Overlay)
    .hidden_title(true)
    .traffic_light_position(TRAFFIC_LIGHTS)
    .inner_size(640.0, 780.0)
    .min_inner_size(420.0, 600.0)
    .disable_drag_drop_handler()
    .on_navigation(installer::is_installer_page)
    .on_new_window(|_url, _features| NewWindowResponse::Deny)
    .on_permission_request(|_webview, _kind| PermissionResponse::Deny)
    .build()
}

/// Close the installer's window for good: the install is over and the UI takes its place.
fn close_installer(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(installer::WINDOW) {
        let _ = window.destroy();
    }
}

fn show(app: &AppHandle) {
    let window = app
        .get_webview_window(WINDOW)
        .or_else(|| app.get_webview_window(installer::WINDOW));
    if let Some(window) = window {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

/// Whether `page` is the down page (`down_url`), which reads what it says from its fragment.
fn is_down_page(page: &url::Url) -> bool {
    page.scheme() == "tauri" && page.path() == format!("/{DOWN_PAGE}")
}

/// Bring the window up on the new chat page; on the down page, bring it up as it is, as the address of a new chat would
/// wipe what the page says (item 351). The UI opens once the daemon answers.
fn new_chat(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(WINDOW) {
        match window.url() {
            Ok(page) if is_down_page(&page) => {}
            Ok(_) => {
                let _ = window.eval(
                    "location.hash = '#/runs'; if (window.focusComposer) { window.focusComposer(); }",
                );
            }
            Err(error) => {
                eprintln!(
                    "dot-shell: the window's address could not be read for a new chat: {error}"
                )
            }
        }
    }
    show(app);
}

/// Open `target` in the window.
fn open(app: &AppHandle, target: &str) {
    let Some(window) = app.get_webview_window(WINDOW) else {
        return;
    };
    if let Ok(url) = url::Url::parse(target) {
        let _ = window.navigate(url);
    }
}

/// Open the UI of a daemon that has just proven itself (`look`), once its pages have the shell's commands: in the
/// window that shows the down page, or, when the installer was all there was, in the window made for it.
fn open_ui(app: &AppHandle, daemon: &Daemon) {
    if let Err(error) = grant(app, daemon) {
        eprintln!("dot-shell: the daemon's pages could not be given the shell's commands: {error}");
    }
    let target = ui_url(daemon);
    if app.get_webview_window(WINDOW).is_some() {
        open(app, &target);
        return;
    }
    match url::Url::parse(&target) {
        Ok(page) => {
            if let Err(error) = window(app, WebviewUrl::External(page), true) {
                eprintln!("dot-shell: the window of the UI could not be made: {error}");
            }
        }
        Err(error) => eprintln!("dot-shell: the daemon's address is not an address: {error}"),
    }
}

/// Whether the window leaves the UI for the down page after `misses` polls in a row that did not count.
fn leaves_ui(misses: u32) -> bool {
    misses >= MISSES_BEFORE_DOWN
}

/// Whether a look at the releases is due: `UPDATE_EVERY` after the last, by the wall clock. A clock set back before the
/// last look makes one due, rather than none for as long as it was set back.
fn update_due(last: SystemTime, now: SystemTime) -> bool {
    now.duration_since(last)
        .map_or(true, |since| since >= UPDATE_EVERY)
}

/// Make the shell's login item what `[desktop_app] start_at_login` says (`login_item`), for an app run from its bundle;
/// a build run from elsewhere has none and touches none. What could not be done is said, and tried again at the next
/// change or start.
fn keep_login_item(start_at_login: bool) {
    let bundle = match env::current_exe() {
        Ok(exe) => login_item::bundle_of(&exe),
        Err(error) => {
            eprintln!("dot-shell: the app's own path is not known, so its login item is left as it is: {error}");
            return;
        }
    };
    let Some(bundle) = bundle else {
        return;
    };
    let Ok(home) = env::var("HOME") else {
        eprintln!("dot-shell: HOME is not set, so the login item is left as it is");
        return;
    };
    let path = login_item::job_path(Path::new(&home));
    let job = start_at_login.then(|| login_item::job(&bundle));
    match login_item::apply(&path, job.as_deref()) {
        Ok(login_item::Done::AsItWas) => {}
        Ok(done) => eprintln!("dot-shell: login item {done:?}: {}", path.display()),
        Err(error) => eprintln!(
            "dot-shell: the login item {} could not be changed: {error}",
            path.display()
        ),
    }
}

/// Every second: the count for the menu bar item, and the window's page when the daemon comes or goes or what keeps
/// the shell from it changes. `shown` is the page `setup` opened: the window leaves the UI after `MISSES_BEFORE_DOWN`
/// polls in a row that did not count, and opens it again at the first that does. `[desktop_app]` is applied when it
/// changes (the login item, the language of the menu, the tip and the down page), and the releases are looked at once
/// `UPDATE_EVERY` has passed since the look at start: a comparison of two times a second, no timer of its own.
fn watch(app: AppHandle, shown: Shown) {
    thread::spawn(move || {
        let mut shown = shown;
        let mut misses: u32 = 0;
        let mut shown_look: Option<TrayLook> = None;
        let mut login_applied: Option<bool> = None;
        let mut last_update_look = SystemTime::now();
        loop {
            let Polled {
                seen,
                daemon,
                app: settings,
            } = poll(&dot_home());
            if let Some(settings) = settings {
                if login_applied != Some(settings.start_at_login) {
                    keep_login_item(settings.start_at_login);
                    login_applied = Some(settings.start_at_login);
                }
                if settings.lang != lang_of(&app) {
                    *app.state::<Speech>()
                        .0
                        .lock()
                        .expect("nothing panics while it holds the language") = settings.lang;
                    if let Err(error) = redraw_menu(&app) {
                        eprintln!(
                            "dot-shell: the menu could not be drawn in its new language: {error}"
                        );
                    }
                }
            }
            let lang = lang_of(&app);
            let now = SystemTime::now();
            if update_due(last_update_look, now) {
                last_update_look = now;
                check_for_update(app.clone(), UpdateCheck::Daily);
            }
            let counted = matches!(seen, Seen::Counted(_));
            misses = if counted { 0 } else { misses.saturating_add(1) };
            // The item is set only when what it shows changes (item 2089), not at every poll: each set decodes the
            // icon's PNG and hands the app's main thread three calls to AppKit.
            let look = tray_look(
                &seen,
                matches!(shown, Shown::Installer),
                download_of(&app),
                lang.words(),
            );
            if shown_look.as_ref() != Some(&look) {
                if let Some(tray) = app.tray_by_id(TRAY) {
                    shown_look = show_look(&tray, &look).then_some(look);
                }
            }
            shown = match (shown, daemon) {
                // The installer's window gives way once the daemon answers and the install is not at work: the
                // daemon the install's last step waited for, late or not, or a Dot that is not of this install. The
                // answer decides nothing about the install: it is complete when its service is installed, which the
                // marker records as that step ends (`installer::record`), so that no start depends on this poll.
                (Shown::Installer, Some(daemon)) if counted && !installing(&app) => {
                    close_installer(&app);
                    open_ui(&app, &daemon);
                    Shown::Ui(ui_url(&daemon))
                }
                (Shown::Installer, _) => Shown::Installer,
                (Shown::Ui(old), Some(daemon)) if counted && ui_url(&daemon) != old => {
                    open_ui(&app, &daemon);
                    Shown::Ui(ui_url(&daemon))
                }
                (Shown::Ui(old), _) if !leaves_ui(misses) => Shown::Ui(old),
                (Shown::Down(_), Some(daemon)) if counted => {
                    open_ui(&app, &daemon);
                    Shown::Ui(ui_url(&daemon))
                }
                (shown, _) => {
                    let page = down_url(&seen, lang);
                    if !matches!(&shown, Shown::Down(old) if *old == page) {
                        open(&app, &page);
                    }
                    Shown::Down(page)
                }
            };
            thread::sleep(POLL);
        }
    });
}

/// Who asked for a look at the releases (item 432): the owner, from the menu, is told also that Dot is the newest or
/// why it could not look; the look Dot takes as it starts, and once a day after, speaks only when it found a newer Dot.
#[derive(Clone, Copy, PartialEq, Eq)]
enum UpdateCheck {
    Asked,
    AtStart,
    Daily,
}

/// Whether a look at the releases is under way, its offer and the install included: one at a time, so that the look at
/// start and the menu's, or two clicks of the menu, never install twice. A message that only tells what a look found
/// is not part of it.
static LOOKING: AtomicBool = AtomicBool::new(false);

/// The look at the releases under way: it ends when this is dropped, on every way out.
struct Looking;

impl Looking {
    /// The look, unless another is under way.
    fn start() -> Option<Looking> {
        LOOKING
            .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
            .ok()
            .map(|_| Looking)
    }
}

impl Drop for Looking {
    fn drop(&mut self) {
        LOOKING.store(false, Ordering::SeqCst);
    }
}

/// A native message with only an OK button, for what a look at the releases found. The dialogs are the system's, shown
/// by the shell itself (rfd): no page gets a way to show one.
async fn tell(level: MessageLevel, message: String) {
    AsyncMessageDialog::new()
        .set_title("Dot")
        .set_level(level)
        .set_description(message)
        .set_buttons(MessageButtons::Ok)
        .show()
        .await;
}

/// Look for a newer Dot among the releases (`plugins.updater` of tauri.conf.json, item 432). One found is offered;
/// on yes it is downloaded, its signature checked against the public key the shell carries, installed, and the shell
/// started again.
fn check_for_update(app: AppHandle, how: UpdateCheck) {
    tauri::async_runtime::spawn(async move {
        let words = lang_of(&app).words();
        let Some(looking) = Looking::start() else {
            if how == UpdateCheck::Asked {
                let said = match download_of(&app) {
                    Some(progress) => fill(
                        words.downloading_already,
                        &[("progress", &progress.short())],
                    ),
                    None => words.looking_already.to_string(),
                };
                tell(MessageLevel::Info, said).await;
            }
            return;
        };
        let found = match app.updater() {
            Ok(updater) => updater.check().await,
            Err(error) => Err(error),
        };
        match found {
            Ok(Some(update)) => offer_update(&app, update, looking, words).await,
            Ok(None) => {
                drop(looking);
                if how == UpdateCheck::Asked {
                    let current = app.package_info().version.to_string();
                    tell(
                        MessageLevel::Info,
                        fill(words.newest, &[("current", &current)]),
                    )
                    .await;
                }
            }
            Err(error) => {
                drop(looking);
                eprintln!("dot-shell: the releases could not be read for an update: {error}");
                if how == UpdateCheck::Asked {
                    tell(
                        MessageLevel::Warning,
                        fill(words.could_not_look, &[("error", &error.to_string())]),
                    )
                    .await;
                }
            }
        }
    });
}

/// Ask whether to install `update`; on yes, download it, with how far it is on the menu bar item (`Downloading`), install
/// it and start the shell again. The look (`looking`) ends with the answer Later, or once the install failed, before that
/// is told.
async fn offer_update(app: &AppHandle, update: Update, looking: Looking, words: &'static Words) {
    let answer = AsyncMessageDialog::new()
        .set_title(words.newer_title)
        .set_level(MessageLevel::Info)
        .set_description(fill(
            words.newer_body,
            &[
                ("version", &update.version),
                ("current", &update.current_version),
            ],
        ))
        .set_buttons(MessageButtons::OkCancelCustom(
            words.install.to_string(),
            words.later.to_string(),
        ))
        .show()
        .await;
    if !matches!(answer, MessageDialogResult::Custom(ref button) if button == words.install) {
        return;
    }
    set_download(
        app,
        Some(Progress {
            received: 0,
            total: None,
            installing: false,
        }),
    );
    let chunks = app.clone();
    let finished = app.clone();
    let installed = update
        .download_and_install(
            move |chunk, total| {
                let held = chunks.state::<Downloading>();
                let mut progress = held
                    .0
                    .lock()
                    .expect("nothing panics while it holds the download");
                if let Some(progress) = progress.as_mut() {
                    progress.received = progress.received.saturating_add(chunk as u64);
                    progress.total = total;
                }
            },
            move || {
                set_download(
                    &finished,
                    download_of(&finished).map(|progress| Progress {
                        installing: true,
                        ..progress
                    }),
                );
            },
        )
        .await;
    match installed {
        Ok(()) => {
            if let Some(window) = app.get_webview_window(WINDOW) {
                save_place(&window);
            }
            app.restart()
        }
        Err(error) => {
            set_download(app, None);
            drop(looking);
            eprintln!("dot-shell: the update could not be installed: {error}");
            tell(
                MessageLevel::Warning,
                fill(words.could_not_install, &[("error", &error.to_string())]),
            )
            .await;
        }
    }
}

/// The menu of the menu bar item, in the shell's language. `shortcut_taken` says that ⇧⌘Space could not be had: the
/// menu then says so, and New chat is only in the menu.
fn tray_menu(app: &AppHandle, words: &Words, shortcut_taken: bool) -> tauri::Result<Menu<Wry>> {
    let open = MenuItem::with_id(app, "open", words.open, true, None::<&str>)?;
    let chat = MenuItem::with_id(
        app,
        "chat",
        words.new_chat,
        true,
        (!shortcut_taken).then_some("CmdOrCtrl+Shift+Space"),
    )?;
    let taken = MenuItem::with_id(app, "shortcut", words.shortcut_taken, false, None::<&str>)?;
    let update = MenuItem::with_id(app, "update", words.check_updates, true, None::<&str>)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let before_quit = PredefinedMenuItem::separator(app)?;
    let quit = PredefinedMenuItem::quit(app, Some(words.quit))?;
    if shortcut_taken {
        Menu::with_items(
            app,
            &[
                &open,
                &chat,
                &taken,
                &separator,
                &update,
                &before_quit,
                &quit,
            ],
        )
    } else {
        Menu::with_items(
            app,
            &[&open, &chat, &separator, &update, &before_quit, &quit],
        )
    }
}

/// Draw the menu again, in the language the shell now speaks.
fn redraw_menu(app: &AppHandle) -> tauri::Result<()> {
    let Some(tray) = app.tray_by_id(TRAY) else {
        return Ok(());
    };
    let taken = app.state::<ShortcutTaken>().0;
    tray.set_menu(Some(tray_menu(app, lang_of(app).words(), taken)?))
}

/// The menu bar item, with its menu (`tray_menu`).
fn tray(app: &AppHandle, shortcut_taken: bool) -> tauri::Result<()> {
    let menu = tray_menu(app, lang_of(app).words(), shortcut_taken)?;
    TrayIconBuilder::with_id(TRAY)
        .icon(tray_icon(Face::Idle))
        .tooltip("Dot")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open" => show(app),
            "chat" => new_chat(app),
            "update" => check_for_update(app.clone(), UpdateCheck::Asked),
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                show(tray.app_handle());
            }
        })
        .build(app)?;
    Ok(())
}

fn shortcut(app: &AppHandle) -> tauri::Result<()> {
    let combo = Shortcut::new(Some(Modifiers::SUPER | Modifiers::SHIFT), Code::Space);
    app.plugin(
        tauri_plugin_global_shortcut::Builder::new()
            .with_handler(move |app, pressed, event| {
                if pressed == &combo && event.state() == ShortcutState::Pressed {
                    new_chat(app);
                }
            })
            .build(),
    )?;
    app.global_shortcut()
        .register(combo)
        .map_err(|error| tauri::Error::Anyhow(error.into()))?;
    Ok(())
}

/// Whether the install is at work now.
fn installing(app: &AppHandle) -> bool {
    app.try_state::<installer::Installer>()
        .is_some_and(|installer| installer.running())
}

/// With an install the app made, look at every start whether the package it installed is older than the app (the app
/// updated itself), and upgrade it (`installer::keep_in_step`). It says so only when it could not.
fn keep_package_in_step(app: &AppHandle) {
    let Some(ctx) = app.state::<installer::Installer>().context() else {
        return;
    };
    tauri::async_runtime::spawn(async move {
        match tauri::async_runtime::spawn_blocking(move || installer::keep_in_step(&ctx)).await {
            Ok(Ok(Some(said))) => eprintln!("dot-shell: {said}"),
            Ok(Ok(None)) => {}
            Ok(Err(words)) => {
                eprintln!("dot-shell: {words}");
                tell(MessageLevel::Warning, words).await;
            }
            Err(error) => eprintln!("dot-shell: the check of Dot's engine did not end: {error}"),
        }
    });
}

/// The app, built from `builder` with the commands of the installer's page (item 575) and the capabilities of this crate
/// (`capabilities/`): Tauri's ACL lets only that window's own page call the commands. It is the one place
/// `generate_context!` is written, which embeds symbols a second use would define twice, so the tests build the app the
/// same way, with the mock runtime, and ask it for the commands from the pages that must be refused.
fn build_app<R: tauri::Runtime>(builder: tauri::Builder<R>) -> tauri::App<R> {
    builder
        .invoke_handler(tauri::generate_handler![
            installer::installer_inspect,
            installer::installer_status,
            installer::installer_start,
            installer::installer_retry,
            installer::installer_open,
        ])
        .build(tauri::generate_context!())
        .expect("the desktop shell could not start")
}

pub fn run() {
    let builder = tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(Granted::default())
        .manage(Downloading::default())
        .manage(SavedPlace::default())
        .setup(|app| {
            let handle = app.handle().clone();
            let context = installer::context_of(&handle, dot_home());
            let installing = context.as_ref().is_some_and(installer::wanted);
            app.manage(installer::Installer::new(context));
            // Opened at login by its login item, the shell starts in the menu bar: the window is made hidden.
            let visible = !env::args().any(|argument| argument == login_item::AT_LOGIN);
            let shown = if installing {
                app.manage(Speech(Mutex::new(Lang::En)));
                installer_window(&handle)?;
                Shown::Installer
            } else {
                let Polled {
                    seen,
                    daemon,
                    app: settings,
                } = poll(&dot_home());
                let lang = settings.map_or(Lang::En, |settings| settings.lang);
                app.manage(Speech(Mutex::new(lang)));
                match daemon {
                    Some(daemon) if matches!(seen, Seen::Counted(_)) => {
                        grant(&handle, &daemon)?;
                        window(
                            &handle,
                            WebviewUrl::External(url::Url::parse(&ui_url(&daemon))?),
                            visible,
                        )?;
                        Shown::Ui(ui_url(&daemon))
                    }
                    _ => {
                        let page = down_url(&seen, lang);
                        window(
                            &handle,
                            WebviewUrl::External(url::Url::parse(&page)?),
                            visible,
                        )?;
                        Shown::Down(page)
                    }
                }
            };
            // Another app may hold the shortcut: the shell then goes on without it, and its menu says so.
            let shortcut_taken = match shortcut(&handle) {
                Ok(()) => false,
                Err(error) => {
                    eprintln!("dot-shell: the shortcut ⇧⌘Space could not be registered: {error}");
                    true
                }
            };
            app.manage(ShortcutTaken(shortcut_taken));
            tray(&handle, shortcut_taken)?;
            check_for_update(handle.clone(), UpdateCheck::AtStart);
            if !installing {
                keep_package_in_step(&handle);
            }
            watch(handle, shown);
            Ok(())
        })
        // Closing the window keeps the shell in the menu bar; the window hides and comes back from there. Its place is
        // saved as it hides or loses the focus, not while it moves.
        .on_window_event(|window, event| {
            let main = window
                .app_handle()
                .get_webview_window(WINDOW)
                .filter(|main| main.label() == window.label());
            match event {
                WindowEvent::CloseRequested { api, .. } => {
                    api.prevent_close();
                    if let Some(main) = &main {
                        save_place(main);
                    }
                    let _ = window.hide();
                }
                WindowEvent::Focused(false) => {
                    if let Some(main) = &main {
                        save_place(main);
                    }
                }
                _ => {}
            }
        });
    let app = build_app(builder);
    app.run(|app, event| match event {
        RunEvent::Reopen { .. } => show(app),
        // An install that is at work ends with the app: its program is stopped, not left to run on alone.
        RunEvent::Exit => {
            if let Some(window) = app.get_webview_window(WINDOW) {
                save_place(&window);
            }
            if let Some(installer) = app.try_state::<installer::Installer>() {
                installer.stop();
            }
        }
        _ => {}
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::{TcpListener, TcpStream};
    use std::os::unix::fs::PermissionsExt;
    use std::sync::mpsc::{self, Receiver};
    use tauri::ipc::RuntimeCapability;
    use tauri::utils::acl::capability::{CapabilityFile, PermissionEntry};
    use tauri::utils::acl::RemoteUrlPattern;

    const TOKEN: &str = "MARKER-0123456789abcdef";
    const NONCE: &str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
    /// `daemon_proof(TOKEN, NONCE)` of `dotagent.server.guard`; tests/unit/test_server_guard.py holds the same pair.
    const PROOF: &str = "4aea8baff569980b8b9dceb16e4d02f89d6563a252c47dcf94912b82ae440020";
    const COUNT: &str = r#"{"waiting": 1, "working": 0, "state": "waiting"}"#;
    /// What macOS says of a file its owner may not read.
    const DENIED: &str = "Permission denied (os error 13)";

    /// How a program on the daemon's port answers a request: the status line's end and the body.
    type Answer = fn(&str) -> (&'static str, String);

    fn read_request(stream: &mut TcpStream) -> String {
        let mut request = Vec::new();
        let mut buffer = [0u8; 1024];
        while !request.windows(4).any(|window| window == b"\r\n\r\n") {
            match stream.read(&mut buffer) {
                Ok(0) | Err(_) => break,
                Ok(read) => request.extend_from_slice(&buffer[..read]),
            }
        }
        String::from_utf8_lossy(&request).into_owned()
    }

    /// A program on a free port of this Mac that answers each request with `answer`, and hands each request over as
    /// it came, before it answers.
    fn listener(answer: Answer) -> (Daemon, Receiver<String>) {
        let socket = TcpListener::bind("127.0.0.1:0").expect("a free port");
        let base = format!(
            "http://{}",
            socket.local_addr().expect("the port's address")
        );
        let (hand_over, requests) = mpsc::channel();
        thread::spawn(move || {
            for stream in socket.incoming() {
                let Ok(mut stream) = stream else {
                    return;
                };
                let request = read_request(&mut stream);
                let (status, body) = answer(&request);
                let _ = hand_over.send(request);
                let _ = write!(
                    stream,
                    "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                );
            }
        });
        let daemon = Daemon {
            base,
            token: TOKEN.to_string(),
        };
        (daemon, requests)
    }

    /// The daemon: the proof of the nonce it is sent, and the count to any other request.
    fn daemon_like(request: &str) -> (&'static str, String) {
        let line = request.lines().next().unwrap_or_default();
        match line
            .strip_prefix("GET /api/proof?nonce=")
            .and_then(|rest| rest.split(' ').next())
        {
            Some(nonce) => {
                let mut mac = HmacSha256::new_from_slice(TOKEN.as_bytes())
                    .expect("HMAC takes a key of any length");
                mac.update(PROOF_LABEL);
                mac.update(nonce.as_bytes());
                let proof = hex::encode(mac.finalize().into_bytes());
                ("200 OK", format!(r#"{{"proof": "{proof}"}}"#))
            }
            None => ("200 OK", COUNT.to_string()),
        }
    }

    /// Another program on the port: every request is answered with a count and a proof that is not the token's.
    fn stranger(_request: &str) -> (&'static str, String) {
        let proof = "00".repeat(32);
        let body =
            format!(r#"{{"proof": "{proof}", "waiting": 1, "working": 0, "state": "waiting"}}"#);
        ("200 OK", body)
    }

    /// A program that knows no `/api/proof`, as the daemon of before this check answers it.
    fn refuser(_request: &str) -> (&'static str, String) {
        let body = r#"{"message": "a valid bearer token is required"}"#.to_string();
        ("401 Unauthorized", body)
    }

    fn carries_the_token(request: &str) -> bool {
        request.contains(TOKEN) || request.to_ascii_lowercase().contains("authorization:")
    }

    /// The home of one test in the system's temporary folder, removed with what it holds when the test ends.
    struct Home(PathBuf);

    impl Home {
        fn new(test: &str) -> Home {
            let path = env::temp_dir().join(format!("dot-shell-{}-{test}", std::process::id()));
            if let Err(error) = fs::remove_dir_all(&path) {
                assert_eq!(error.kind(), io::ErrorKind::NotFound, "{error}");
            }
            fs::create_dir_all(&path).expect("a folder in the temporary folder");
            Home(path)
        }

        fn write(&self, name: &str, text: &str) {
            fs::write(self.0.join(name), text).expect("a file of the home");
        }

        /// Make the file `name` one that nobody may read.
        fn refuse(&self, name: &str) {
            fs::set_permissions(self.0.join(name), fs::Permissions::from_mode(0o000))
                .expect("the file's permissions");
        }
    }

    impl Drop for Home {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn the_proof_is_the_one_the_daemon_gives() {
        assert!(proves(TOKEN, NONCE, PROOF));
        assert!(!proves("another token", NONCE, PROOF));
        assert!(!proves(TOKEN, &NONCE.replace('0', "f"), PROOF));
        assert!(!proves(TOKEN, NONCE, &PROOF[..62]));
        assert!(!proves(TOKEN, NONCE, "not hex"));
    }

    #[test]
    fn the_daemon_is_sent_the_token_once_it_has_proven_it_holds_it() {
        let (daemon, requests) = listener(daemon_like);
        assert!(matches!(
            look(&daemon),
            Seen::Counted(Count { waiting: 1, .. })
        ));
        let seen: Vec<String> = requests.try_iter().collect();
        assert_eq!(seen.len(), 2);
        assert!(seen[0].starts_with("GET /api/proof?nonce=") && !carries_the_token(&seen[0]));
        assert!(seen[1].starts_with("GET /api/activity/count ") && carries_the_token(&seen[1]));
    }

    #[test]
    fn a_program_without_the_proof_is_sent_no_token() {
        for answer in [stranger as Answer, refuser] {
            let (daemon, requests) = listener(answer);
            assert!(matches!(look(&daemon), Seen::Unproven(address) if address == daemon.base));
            let seen: Vec<String> = requests.try_iter().collect();
            assert_eq!(seen.len(), 1);
            assert!(!carries_the_token(&seen[0]));
        }
    }

    #[test]
    fn nothing_at_the_address_is_silence() {
        let base = {
            let socket = TcpListener::bind("127.0.0.1:0").expect("a free port");
            format!(
                "http://{}",
                socket.local_addr().expect("the port's address")
            )
        };
        let daemon = Daemon {
            base,
            token: TOKEN.to_string(),
        };
        assert!(matches!(look(&daemon), Seen::Silent));
    }

    #[test]
    fn the_daemon_is_looked_for_where_its_files_say() {
        let home = Home::new("found");
        home.write(TOKEN_FILE, &format!("{TOKEN}\n"));
        let found = read_daemon(&home.0).expect("the daemon's files are read");
        assert_eq!(found.base, format!("http://{DEFAULT_HOST}:{DEFAULT_PORT}"));
        assert_eq!(found.token, TOKEN);
        home.write(CONFIG_FILE, "[server]\nhost = \"::1\"\nport = 9001\n");
        let moved = read_daemon(&home.0).expect("the daemon's files are read");
        assert_eq!(moved.base, "http://[::1]:9001");
    }

    #[test]
    fn a_config_that_is_not_toml_is_told_with_its_line_and_no_address_is_guessed() {
        let home = Home::new("config");
        home.write(TOKEN_FILE, TOKEN);
        let config = home.0.join(CONFIG_FILE);
        for (text, place) in [
            ("[server]\nport = 8765\nhost = \n", "line 3, column 8: "),
            ("[server]\nport = \"8765\"\n", "line 2, column 8: "),
        ] {
            home.write(CONFIG_FILE, text);
            let Err(Unread::Config { path, error }) = read_daemon(&home.0) else {
                panic!("{text:?} is read");
            };
            assert_eq!(path, config);
            assert!(error.starts_with(place) && !error.contains('\n'), "{error}");
        }
    }

    #[test]
    fn a_file_the_shell_may_not_read_is_told_in_the_systems_words() {
        let home = Home::new("refused");
        home.write(CONFIG_FILE, "[server]\nport = 8765\n");
        home.write(TOKEN_FILE, TOKEN);
        home.refuse(TOKEN_FILE);
        let Err(Unread::Token { path, error }) = read_daemon(&home.0) else {
            panic!("a token it may not read is read");
        };
        assert_eq!((path, error.as_str()), (home.0.join(TOKEN_FILE), DENIED));
        home.refuse(CONFIG_FILE);
        let Err(Unread::Config { path, error }) = read_daemon(&home.0) else {
            panic!("a config it may not read is read");
        };
        assert_eq!((path, error.as_str()), (home.0.join(CONFIG_FILE), DENIED));
    }

    #[test]
    fn a_home_without_a_token_or_with_an_empty_one_is_told() {
        let home = Home::new("token");
        let token = home.0.join(TOKEN_FILE);
        assert_eq!(
            read_daemon(&home.0).err(),
            Some(Unread::NoToken {
                path: token.clone()
            })
        );
        home.write(TOKEN_FILE, " \n");
        assert_eq!(
            read_daemon(&home.0).err(),
            Some(Unread::EmptyToken { path: token })
        );
    }

    #[test]
    fn a_restart_of_the_daemon_leaves_the_open_page_and_a_daemon_gone_for_longer_does_not() {
        // The longest stop of the daemon to its start in its own log, as the shell's polls see it: one a second.
        let restart_polls: u32 = 6;
        assert!((0..=restart_polls).all(|misses| !leaves_ui(misses)));
        assert!(leaves_ui(MISSES_BEFORE_DOWN));
    }

    #[test]
    fn the_menu_bar_item_shows_the_count_and_changes_only_with_what_it_shows() {
        let counted = |waiting: u64, working: u64, state: &str| {
            Seen::Counted(Count {
                waiting,
                working,
                state: state.to_string(),
            })
        };
        let waiting = tray_look(&counted(2, 1, "waiting"), false, None, Lang::En.words());
        assert_eq!(
            waiting,
            TrayLook {
                face: Face::Waiting,
                title: Some("2".to_string()),
                tip: "Dot · 2 waiting for you · 1 working".to_string(),
            }
        );
        // The same count a second later is the same look, so the item is not set again.
        assert_eq!(
            tray_look(&counted(2, 1, "waiting"), false, None, Lang::En.words()),
            waiting
        );
        let idle = tray_look(&counted(0, 0, "idle"), false, None, Lang::En.words());
        assert_eq!((idle.face, idle.title), (Face::Idle, None));
        assert_eq!(
            tray_look(&counted(0, 1, "working"), false, None, Lang::En.words()).face,
            Face::Working
        );
        assert_eq!(
            tray_look(&counted(0, 0, "new"), false, None, Lang::En.words()).face,
            Face::Idle
        );
        let down = tray_look(&Seen::Silent, false, None, Lang::En.words());
        assert_eq!(
            (down.face, down.title, down.tip.as_str()),
            (Face::Idle, None, "Dot · the daemon is not running")
        );
        assert_eq!(
            tray_look(&Seen::Silent, true, None, Lang::En.words()).tip,
            "Dot · being set up"
        );
        // In Turkish, as the web UI in the window speaks it.
        assert_eq!(
            tray_look(&counted(2, 1, "waiting"), false, None, Lang::Tr.words()).tip,
            "Dot · 2 sizi bekliyor · 1 çalışıyor"
        );
    }

    #[test]
    fn the_menu_bar_item_says_how_far_an_update_has_downloaded() {
        let counted = Seen::Counted(Count {
            waiting: 2,
            working: 0,
            state: "waiting".to_string(),
        });
        let half = Progress {
            received: 5_000_000,
            total: Some(10_000_000),
            installing: false,
        };
        let words = Lang::En.words();
        let look = tray_look(&counted, false, Some(half), words);
        assert_eq!(look.title.as_deref(), Some("2 · ↓ 50%"));
        assert_eq!(look.tip, "Dot · downloading the update · 50%");
        let no_size = Progress {
            total: None,
            ..half
        };
        assert_eq!(
            tray_look(&Seen::Silent, false, Some(no_size), words)
                .title
                .as_deref(),
            Some("↓ 5.0 MB")
        );
        let installing = Progress {
            installing: true,
            ..half
        };
        assert_eq!(
            tray_look(&counted, false, Some(installing), words).tip,
            "Dot · installing the update"
        );
        // Each chunk changes the look, which the item shows at the next poll: once a second, not once a chunk.
        let more = Progress {
            received: 6_000_000,
            ..half
        };
        assert_ne!(tray_look(&counted, false, Some(more), words), look);
        assert_eq!(
            Progress {
                received: 20,
                total: Some(10),
                installing: false
            }
            .short(),
            "100%"
        );
    }

    #[test]
    fn the_releases_are_looked_at_once_a_day_by_the_wall_clock() {
        let start = SystemTime::UNIX_EPOCH + Duration::from_secs(1_800_000_000);
        assert!(!update_due(start, start));
        assert!(!update_due(
            start,
            start + UPDATE_EVERY - Duration::from_secs(1)
        ));
        assert!(update_due(start, start + UPDATE_EVERY));
        // A clock set back makes a look due, not one put off for as long.
        assert!(update_due(start, start - Duration::from_secs(60)));
    }

    #[test]
    fn the_desktop_apps_settings_are_read_from_the_daemons_config() {
        let home = Home::new("desktop-app");
        let read = |home: &Home| DesktopApp::of(&read_config(&home.0).expect("the config is read"));
        assert_eq!(
            read(&home),
            DesktopApp {
                start_at_login: true,
                lang: Lang::En
            }
        );
        home.write(
            CONFIG_FILE,
            "[server]\nport = 9001\n\n[desktop_app]\nstart_at_login = false\nlanguage = \"tr\"\n",
        );
        assert_eq!(
            read(&home),
            DesktopApp {
                start_at_login: false,
                lang: Lang::Tr
            }
        );
        assert_eq!(
            read_base(&home.0).expect("the address"),
            "http://127.0.0.1:9001"
        );
        // The shell reads the settings with the token missing, so that the down page speaks the language.
        let polled = poll(&home.0);
        assert!(matches!(polled.seen, Seen::Unread(Unread::NoToken { .. })));
        assert_eq!(polled.app.map(|app| app.lang), Some(Lang::Tr));
    }

    #[test]
    fn a_new_chat_leaves_the_down_page_as_it_is_and_opens_on_the_ui() {
        let page = |address: &str| url::Url::parse(address).expect("an address");
        assert!(is_down_page(&page(&down_url(&Seen::Silent, Lang::En))));
        assert!(is_down_page(&page(&down_url(&Seen::Silent, Lang::Tr))));
        assert!(is_down_page(&page(
            "tauri://localhost/down.html#title=Dot+cannot+read&detail=x"
        )));
        assert!(!is_down_page(&page("http://127.0.0.1:8765/#/runs")));
        assert!(!is_down_page(&page("tauri://localhost/index.html")));
    }

    #[test]
    fn the_down_page_is_told_what_kept_the_shell_from_the_daemon() {
        assert_eq!(
            down_url(&Seen::Silent, Lang::En),
            "tauri://localhost/down.html"
        );
        assert_eq!(
            down_url(&Seen::Silent, Lang::Tr),
            "tauri://localhost/down.html#lang=tr"
        );
        let told = |unread: Unread| -> Vec<(String, String)> {
            let page =
                url::Url::parse(&down_url(&Seen::Unread(unread), Lang::En)).expect("an address");
            assert_eq!(
                page.as_str().split('#').next(),
                Some("tauri://localhost/down.html")
            );
            let fragment = page.fragment().expect("what the page is told");
            url::form_urlencoded::parse(fragment.as_bytes())
                .into_owned()
                .collect()
        };
        let pair = |name: &str, value: &str| (name.to_string(), value.to_string());
        let config = PathBuf::from("/Users/someone/Library/Application Support/Dot/config.toml");
        let error = "line 3, column 8: invalid string; expected `\"`, `'` & #more";
        assert_eq!(
            told(Unread::Config {
                path: config.clone(),
                error: error.to_string()
            }),
            vec![
                pair("title", "Dot could not read its config.toml"),
                pair("detail", &format!("{}: {error}", config.display())),
            ]
        );
        let token = PathBuf::from("/Users/someone/Library/Application Support/Dot/api-token");
        assert_eq!(
            told(Unread::NoToken {
                path: token.clone()
            }),
            vec![pair(
                "detail",
                &format!(
                    "{}: there is none yet; the daemon makes it the first time it starts",
                    token.display()
                )
            )]
        );
        let turkish = url::Url::parse(&down_url(
            &Seen::Unread(Unread::EmptyToken {
                path: token.clone(),
            }),
            Lang::Tr,
        ))
        .expect("an address");
        let told: Vec<(String, String)> = url::form_urlencoded::parse(
            turkish
                .fragment()
                .expect("what the page is told")
                .as_bytes(),
        )
        .into_owned()
        .collect();
        assert_eq!(
            told,
            vec![
                pair("lang", "tr"),
                pair("title", "Dot, anahtarını (token) okuyamadı"),
                pair(
                    "detail",
                    &format!(
                        "{}: boş; silin, arka plan hizmeti başladığında yenisini oluşturur",
                        token.display()
                    )
                ),
            ]
        );
    }

    #[test]
    fn the_down_page_says_that_something_else_answers_at_the_daemons_address() {
        let told = {
            let page = url::Url::parse(&down_url(
                &Seen::Unproven("http://127.0.0.1:8765".to_string()),
                Lang::En,
            ))
            .expect("an address");
            assert_eq!(
                page.as_str().split('#').next(),
                Some("tauri://localhost/down.html")
            );
            url::form_urlencoded::parse(page.fragment().expect("what the page is told").as_bytes())
                .into_owned()
                .collect::<Vec<(String, String)>>()
        };
        assert_eq!(told.len(), 2);
        assert_eq!(
            told[0],
            (
                "title".to_string(),
                "Something else answers at http://127.0.0.1:8765".to_string()
            )
        );
        assert_eq!(told[1].0, "detail");
        assert!(told[1].1.contains("lsof -nP -iTCP:8765 -sTCP:LISTEN"));
        assert!(!told[1].1.contains("  "), "{}", told[1].1);
        assert!(unproven_detail("not an address", Lang::En.words())
            .contains("lsof -nP -iTCP -sTCP:LISTEN"));
    }

    #[test]
    fn only_the_pages_of_the_daemons_own_address_may_use_the_shell() {
        let daemon = Daemon {
            base: "http://127.0.0.1:8765".to_string(),
            token: TOKEN.to_string(),
        };
        let CapabilityFile::Capability(capability) = daemon_capability(&daemon).build() else {
            panic!("one capability");
        };
        let urls = capability
            .remote
            .expect("a capability of remote pages")
            .urls;
        assert_eq!(urls, vec![daemon.base.clone()]);
        assert!(!capability.local);
        assert_eq!(capability.windows, vec![WINDOW.to_string()]);
        let permissions: Vec<&str> = capability
            .permissions
            .iter()
            .map(|entry| match entry {
                PermissionEntry::PermissionRef(identifier) => identifier.get(),
                PermissionEntry::ExtendedPermission { identifier, .. } => identifier.get(),
            })
            .collect();
        assert_eq!(permissions, PAGE_PERMISSIONS);
        let pattern: RemoteUrlPattern = urls[0].parse().expect("a URL pattern");
        let reaches = |page: &str| pattern.test(&url::Url::parse(page).expect("an address"));
        assert!(reaches("http://127.0.0.1:8765/#token=x"));
        assert!(reaches("http://127.0.0.1:8765/#/runs/run_1"));
        assert!(!reaches("http://127.0.0.1:8766/"));
        assert!(!reaches("http://localhost:8765/"));
        assert!(!reaches("https://127.0.0.1:8765/"));
    }

    #[test]
    fn only_a_page_of_the_daemons_address_is_given_the_microphone_and_nothing_else_is_given() {
        let granted: HashSet<String> = HashSet::from(["http://127.0.0.1:8765".to_string()]);
        let page = |address: &str| url::Url::parse(address).expect("a page's address");
        let given = |address: Option<&str>, kind: PermissionKind| {
            let page = address.map(page);
            matches!(
                permission_for(page.as_ref(), kind, &granted),
                PermissionResponse::Allow
            )
        };
        // The daemon's page, the composer's dictation (item 594).
        assert!(given(
            Some("http://127.0.0.1:8765/#/runs"),
            PermissionKind::Microphone
        ));
        // Nothing else of the web view, not even to the daemon's page.
        assert!(!given(
            Some("http://127.0.0.1:8765/#/runs"),
            PermissionKind::Camera
        ));
        assert!(!given(
            Some("http://127.0.0.1:8765/#/runs"),
            PermissionKind::Geolocation
        ));
        // A page a link led the window to, another port of this Mac, the shell's own pages, or no page at all.
        for address in [
            "https://example.org/listen",
            "http://127.0.0.1:9000/",
            "http://localhost:8765/",
            "tauri://localhost/down.html",
        ] {
            assert!(
                !given(Some(address), PermissionKind::Microphone),
                "{address}"
            );
        }
        assert!(!given(None, PermissionKind::Microphone));
        // An address as config.toml may spell it is the page's own: a name in capitals, the scheme's own port.
        let spelled: HashSet<String> = HashSet::from([
            "http://LOCALHOST:8765".to_string(),
            "http://127.0.0.1:80".to_string(),
        ]);
        for address in ["http://localhost:8765/#/runs", "http://127.0.0.1/#/runs"] {
            assert!(
                matches!(
                    permission_for(Some(&page(address)), PermissionKind::Microphone, &spelled),
                    PermissionResponse::Allow
                ),
                "{address}"
            );
        }
    }

    #[test]
    fn the_shells_own_pages_are_given_the_same_and_no_remote_page_is() {
        let file: serde_json::Value =
            serde_json::from_str(include_str!("../capabilities/default.json"))
                .expect("the capability file is JSON");
        assert!(file.get("remote").is_none());
        assert_eq!(file["permissions"], serde_json::json!(PAGE_PERMISSIONS));
    }

    #[test]
    fn a_page_is_given_no_more_of_tauris_core_than_the_window_commands_its_bars_use() {
        // `core:default` takes in the image commands (a page could read any PNG from disk), the tray and menu commands
        // (it could remove the menu bar item) and the event commands (item 561).
        for permission in PAGE_PERMISSIONS {
            assert!(
                !permission.starts_with("core:") || permission.starts_with("core:window:allow-"),
                "{permission} grants more of Tauri's core than the window's bars use"
            );
        }
    }
}
