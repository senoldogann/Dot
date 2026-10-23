//! The desktop shell of Dot: a native window around the web UI the daemon serves, a menu bar item with the face of
//! the web UI and the count of what waits for the user, a global shortcut for a new chat, and notifications sent by
//! the web UI through the shell. The daemon stays owned by launchd (`dotx service install`): the shell connects to it
//! with the token of the token file and never runs the agent itself. It sends the token only to a daemon that has
//! just proven it holds it (`GET /api/proof`), so a program that took the daemon's port is sent nothing. The pages of
//! the daemon's own address are the only remote pages that may use the shell's commands (`grant`). While the daemon is
//! down the window shows the page that says how to start it, or which of the daemon's files the shell could not read
//! and why (`Unread`), and opens the UI by itself when the daemon answers.

use std::collections::HashSet;
use std::env;
use std::fs;
use std::io;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::Duration;

use hmac::{Hmac, KeyInit, Mac};
use rfd::{AsyncMessageDialog, MessageButtons, MessageDialogResult, MessageLevel};
use serde::Deserialize;
use sha2::Sha256;
use tauri::image::Image;
use tauri::ipc::CapabilityBuilder;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::webview::NewWindowResponse;
use tauri::{
    AppHandle, LogicalPosition, Manager, RunEvent, TitleBarStyle, WebviewUrl, WebviewWindow,
    WebviewWindowBuilder, WindowEvent,
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};
use tauri_plugin_updater::{Update, UpdaterExt};

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
/// What the down page says of a token file that is there but empty, which the daemon refuses too.
const EMPTY_TOKEN: &str = "it is empty; delete it and the daemon makes a new one when it starts";
/// What the daemon's proof puts before the nonce (`daemon_proof` in `dotagent.server.guard`).
const PROOF_LABEL: &[u8] = b"dot daemon proof\n";
const NONCE_BYTES: usize = 32;
/// What a page of the window may do through the shell: the core commands, dragging the window by its bars, and
/// notifications. `capabilities/default.json` gives the same to the shell's own pages.
const PAGE_PERMISSIONS: [&str; 3] = [
    "core:default",
    "core:window:allow-start-dragging",
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

#[derive(Deserialize, Default)]
struct ConfigFile {
    server: Option<ServerSection>,
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
    /// The token file is there but could not be read, or is empty.
    Token { path: PathBuf, error: String },
    /// There is no token file: the daemon makes it the first time it starts with this home.
    NoToken { path: PathBuf },
}

impl Unread {
    /// What the menu bar item's tip says after "Dot · ".
    fn tip(&self) -> &'static str {
        match self {
            Unread::Config { .. } => "its config.toml could not be read",
            Unread::Token { .. } => "its token could not be read",
            Unread::NoToken { .. } => "the daemon has not made its token yet",
        }
    }

    /// What the down page says instead of "Dot is not running" and the advice to start it: with a file the shell could
    /// not read the daemon may well run. A home without a token is one where the daemon has never started.
    fn title(&self) -> Option<&'static str> {
        match self {
            Unread::Config { .. } => Some("Dot could not read its config.toml"),
            Unread::Token { .. } => Some("Dot could not read its token"),
            Unread::NoToken { .. } => None,
        }
    }

    /// The file and what is wrong with it, as the down page shows it.
    fn detail(&self) -> String {
        match self {
            Unread::Config { path, error } | Unread::Token { path, error } => {
                format!("{}: {error}", path.display())
            }
            Unread::NoToken { path } => format!(
                "{}: there is none yet; the daemon makes it the first time it starts",
                path.display()
            ),
        }
    }
}

/// Where the daemon listens and the token that signs the shell in.
#[derive(Clone)]
struct Daemon {
    base: String,
    token: String,
}

/// The page the window shows (`watch`): the UI, or the down page at the address that says why (`down_url`).
enum Shown {
    Ui,
    Down(String),
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

/// Where the daemon of `home` listens and its token, read as the daemon reads them: without a config.toml it keeps
/// its defaults. What the shell could not read is told (`Unread`), and no address is guessed in its place.
fn read_daemon(home: &Path) -> Result<Daemon, Unread> {
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
    let server = config.server.unwrap_or_default();
    let host = server.host.unwrap_or_else(|| DEFAULT_HOST.to_string());
    let port = server.port.unwrap_or(DEFAULT_PORT);
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
        return Err(Unread::Token {
            path: token_path,
            error: EMPTY_TOKEN.to_string(),
        });
    }
    // An IPv6 host goes in brackets, as `dotagent.server.links.base_url` writes it.
    let bracketed = if host.contains(':') {
        format!("[{host}]")
    } else {
        host
    };
    Ok(Daemon {
        base: format!("http://{bracketed}:{port}"),
        token,
    })
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
fn unproven_detail(address: &str) -> String {
    let listening = match url::Url::parse(address)
        .ok()
        .and_then(|parsed| parsed.port())
    {
        Some(port) => format!("lsof -nP -iTCP:{port} -sTCP:LISTEN"),
        None => "lsof -nP -iTCP -sTCP:LISTEN".to_string(),
    };
    format!(
        "It does not prove that it holds this Dot's token, so the shell sent it nothing: another program holds the \
         port, or a daemon of another Dot home or of an older version answers. See what listens with '{listening}', \
         stop it, and this window opens the web UI by itself."
    )
}

/// The down page, told in its fragment what kept the shell from the daemon (`down.js`): `title` stands for "Dot is
/// not running" and the advice to start it, `detail` says which file and what is wrong with it, or what answers at
/// the daemon's address instead of it. A daemon that does not answer gets the page as it is.
fn down_url(seen: &Seen) -> String {
    let page = format!("tauri://localhost/{DOWN_PAGE}");
    let mut told = url::form_urlencoded::Serializer::new(String::new());
    match seen {
        Seen::Unread(unread) => {
            if let Some(title) = unread.title() {
                told.append_pair("title", title);
            }
            told.append_pair("detail", &unread.detail());
        }
        Seen::Unproven(address) => {
            told.append_pair("title", &format!("Something else answers at {address}"));
            told.append_pair("detail", &unproven_detail(address));
        }
        Seen::Counted(_) | Seen::Silent => return page,
    }
    format!("{page}#{}", told.finish())
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

/// One poll: what the shell reads of the daemon's `home`, then, when it could read it, what answers at its address.
fn poll(home: &Path) -> (Seen, Option<Daemon>) {
    match read_daemon(home) {
        Ok(daemon) => (look(&daemon), Some(daemon)),
        Err(unread) => (Seen::Unread(unread), None),
    }
}

fn tray_icon(state: &str) -> Image<'static> {
    let bytes = match state {
        "waiting" => ICON_WAITING,
        "working" => ICON_WORKING,
        _ => ICON_IDLE,
    };
    Image::from_bytes(bytes)
        .expect("the tray icons are PNG files rendered by desktop/icons/render.py")
}

/// The one window, made on first use, at the size the web UI's screenshot tour reviews (item 88). The title bar is
/// the web UI's own top bars: the system draws only the traffic lights over the page, on the bars' centre line
/// (`TRAFFIC_LIGHTS`), the page keeps room for them (`data-shell="desktop"`, set by the UI when it runs in the shell)
/// and drags the window by its bars. The shell's own drag-and-drop handler is off so the composer's HTML drop works.
/// A link the page opens in a new window (every link of a model's answer) goes to the default browser.
fn window(app: &AppHandle, url: WebviewUrl) -> tauri::Result<WebviewWindow> {
    if let Some(existing) = app.get_webview_window(WINDOW) {
        return Ok(existing);
    }
    WebviewWindowBuilder::new(app, WINDOW, url)
        .title("Dot")
        .title_bar_style(TitleBarStyle::Overlay)
        .hidden_title(true)
        .traffic_light_position(TRAFFIC_LIGHTS)
        .inner_size(1280.0, 860.0)
        .min_inner_size(390.0, 600.0)
        .disable_drag_drop_handler()
        .on_new_window(|url, _features| {
            open_outside(url);
            NewWindowResponse::Deny
        })
        .build()
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

fn show(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(WINDOW) {
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

/// Open the UI of a daemon that has just proven itself (`look`), once its pages have the shell's commands.
fn open_ui(app: &AppHandle, daemon: &Daemon) {
    if let Err(error) = grant(app, daemon) {
        eprintln!("dot-shell: the daemon's pages could not be given the shell's commands: {error}");
    }
    open(app, &ui_url(daemon));
}

/// Whether the window leaves the UI for the down page after `misses` polls in a row that did not count.
fn leaves_ui(misses: u32) -> bool {
    misses >= MISSES_BEFORE_DOWN
}

/// Every second: the count for the menu bar item, and the window's page when the daemon comes or goes or what keeps
/// the shell from it changes. `shown` is the page `setup` opened: the window leaves the UI after `MISSES_BEFORE_DOWN`
/// polls in a row that did not count, and opens it again at the first that does.
fn watch(app: AppHandle, shown: Shown) {
    thread::spawn(move || {
        let mut shown = shown;
        let mut misses: u32 = 0;
        loop {
            let (seen, daemon) = poll(&dot_home());
            let counted = matches!(seen, Seen::Counted(_));
            misses = if counted { 0 } else { misses.saturating_add(1) };
            if let Some(tray) = app.tray_by_id(TRAY) {
                let (state, waiting, working) = match &seen {
                    Seen::Counted(count) => (count.state.as_str(), count.waiting, count.working),
                    Seen::Silent | Seen::Unproven(_) | Seen::Unread(_) => ("idle", 0, 0),
                };
                let _ = tray.set_icon_with_as_template(Some(tray_icon(state)), false);
                let _ = tray.set_title(if waiting > 0 {
                    Some(waiting.to_string())
                } else {
                    None
                });
                let tip = match &seen {
                    Seen::Counted(_) => {
                        format!("Dot · {waiting} waiting for you · {working} working")
                    }
                    Seen::Silent => "Dot · the daemon is not running".to_string(),
                    Seen::Unproven(_) => {
                        "Dot · something else answers at the daemon's address".to_string()
                    }
                    Seen::Unread(unread) => format!("Dot · {}", unread.tip()),
                };
                let _ = tray.set_tooltip(Some(tip));
            }
            shown = match (shown, daemon) {
                (Shown::Ui, _) if !leaves_ui(misses) => Shown::Ui,
                (Shown::Down(_), Some(daemon)) if counted => {
                    open_ui(&app, &daemon);
                    Shown::Ui
                }
                (shown, _) => {
                    let page = down_url(&seen);
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
/// why it could not look; the look Dot takes as it starts speaks only when it found a newer Dot.
#[derive(Clone, Copy, PartialEq, Eq)]
enum UpdateCheck {
    Asked,
    AtStart,
}

/// The button of the offer that installs the update.
const INSTALL: &str = "Install and Restart";

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
        let Some(looking) = Looking::start() else {
            if how == UpdateCheck::Asked {
                tell(
                    MessageLevel::Info,
                    "Dot is looking for an update already.".to_string(),
                )
                .await;
            }
            return;
        };
        let found = match app.updater() {
            Ok(updater) => updater.check().await,
            Err(error) => Err(error),
        };
        match found {
            Ok(Some(update)) => offer_update(&app, update, looking).await,
            Ok(None) => {
                drop(looking);
                if how == UpdateCheck::Asked {
                    let current = app.package_info().version.to_string();
                    tell(MessageLevel::Info, format!("Dot {current} is the newest.")).await;
                }
            }
            Err(error) => {
                drop(looking);
                eprintln!("dot-shell: the releases could not be read for an update: {error}");
                if how == UpdateCheck::Asked {
                    tell(
                        MessageLevel::Warning,
                        format!("Dot could not look for an update: {error}"),
                    )
                    .await;
                }
            }
        }
    });
}

/// Ask whether to install `update`; on yes, install it and start the shell again. The look (`looking`) ends with the
/// answer Later, or once the install failed, before that is told.
async fn offer_update(app: &AppHandle, update: Update, looking: Looking) {
    let answer = AsyncMessageDialog::new()
        .set_title("A newer Dot")
        .set_level(MessageLevel::Info)
        .set_description(format!(
            "Dot {} is out (this one is {}). Install it now? Dot starts again with it.",
            update.version, update.current_version
        ))
        .set_buttons(MessageButtons::OkCancelCustom(
            INSTALL.to_string(),
            "Later".to_string(),
        ))
        .show()
        .await;
    if !matches!(answer, MessageDialogResult::Custom(ref button) if button == INSTALL) {
        return;
    }
    match update.download_and_install(|_, _| {}, || {}).await {
        Ok(()) => app.restart(),
        Err(error) => {
            drop(looking);
            eprintln!("dot-shell: the update could not be installed: {error}");
            tell(
                MessageLevel::Warning,
                format!("The update could not be installed: {error}"),
            )
            .await;
        }
    }
}

/// The menu bar item. `shortcut_taken` says that ⇧⌘Space could not be had: the menu then says so, and New chat is
/// only in the menu.
fn tray(app: &AppHandle, shortcut_taken: bool) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Open Dot", true, None::<&str>)?;
    let chat = MenuItem::with_id(
        app,
        "chat",
        "New chat",
        true,
        (!shortcut_taken).then_some("CmdOrCtrl+Shift+Space"),
    )?;
    let taken = MenuItem::with_id(
        app,
        "shortcut",
        "⇧⌘Space is taken by another app",
        false,
        None::<&str>,
    )?;
    let update = MenuItem::with_id(app, "update", "Check for Updates…", true, None::<&str>)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let before_quit = PredefinedMenuItem::separator(app)?;
    let quit = PredefinedMenuItem::quit(app, Some("Quit Dot"))?;
    let menu = if shortcut_taken {
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
        )?
    } else {
        Menu::with_items(
            app,
            &[&open, &chat, &separator, &update, &before_quit, &quit],
        )?
    };
    TrayIconBuilder::with_id(TRAY)
        .icon(tray_icon("idle"))
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

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .manage(Granted::default())
        .setup(|app| {
            let handle = app.handle().clone();
            let (seen, daemon) = poll(&dot_home());
            let shown = match daemon {
                Some(daemon) if matches!(seen, Seen::Counted(_)) => {
                    grant(&handle, &daemon)?;
                    window(
                        &handle,
                        WebviewUrl::External(url::Url::parse(&ui_url(&daemon))?),
                    )?;
                    Shown::Ui
                }
                _ => {
                    let page = down_url(&seen);
                    window(&handle, WebviewUrl::External(url::Url::parse(&page)?))?;
                    Shown::Down(page)
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
            tray(&handle, shortcut_taken)?;
            check_for_update(handle.clone(), UpdateCheck::AtStart);
            watch(handle, shown);
            Ok(())
        })
        // Closing the window keeps the shell in the menu bar; the window hides and comes back from there.
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("the desktop shell could not start");
    app.run(|app, event| {
        if let RunEvent::Reopen { .. } = event {
            show(app);
        }
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
            Some(Unread::Token {
                path: token,
                error: EMPTY_TOKEN.to_string()
            })
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
    fn a_new_chat_leaves_the_down_page_as_it_is_and_opens_on_the_ui() {
        let page = |address: &str| url::Url::parse(address).expect("an address");
        assert!(is_down_page(&page(&down_url(&Seen::Silent))));
        assert!(is_down_page(&page(
            "tauri://localhost/down.html#title=Dot+cannot+read&detail=x"
        )));
        assert!(!is_down_page(&page("http://127.0.0.1:8765/#/runs")));
        assert!(!is_down_page(&page("tauri://localhost/index.html")));
    }

    #[test]
    fn the_down_page_is_told_what_kept_the_shell_from_the_daemon() {
        assert_eq!(down_url(&Seen::Silent), "tauri://localhost/down.html");
        let told = |unread: Unread| -> Vec<(String, String)> {
            let page = url::Url::parse(&down_url(&Seen::Unread(unread))).expect("an address");
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
    }

    #[test]
    fn the_down_page_says_that_something_else_answers_at_the_daemons_address() {
        let told = {
            let page = url::Url::parse(&down_url(&Seen::Unproven(
                "http://127.0.0.1:8765".to_string(),
            )))
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
        assert!(unproven_detail("not an address").contains("lsof -nP -iTCP -sTCP:LISTEN"));
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
    fn the_shells_own_pages_are_given_the_same_and_no_remote_page_is() {
        let file: serde_json::Value =
            serde_json::from_str(include_str!("../capabilities/default.json"))
                .expect("the capability file is JSON");
        assert!(file.get("remote").is_none());
        assert_eq!(file["permissions"], serde_json::json!(PAGE_PERMISSIONS));
    }
}
