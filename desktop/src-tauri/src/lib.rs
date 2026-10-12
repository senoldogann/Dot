//! The desktop shell of Dot: a native window around the web UI the daemon serves, a menu bar item with the face of
//! the web UI and the count of what waits for the user, a global shortcut for a new chat, and notifications sent by
//! the web UI through the shell. The daemon stays owned by launchd (`dotx service install`): the shell connects to it
//! with the token of the token file and never runs the agent itself. It sends the token only to a daemon that has
//! just proven it holds it (`GET /api/proof`), so a program that took the daemon's port is sent nothing. While the
//! daemon is down the window shows the page that says how to start it, and opens the UI by itself when the daemon
//! answers.

use std::env;
use std::fs;
use std::path::PathBuf;
use std::process::Command;
use std::thread;
use std::time::Duration;

use hmac::{Hmac, Mac};
use serde::Deserialize;
use sha2::Sha256;
use tauri::image::Image;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::webview::NewWindowResponse;
use tauri::{
    AppHandle, LogicalPosition, Manager, RunEvent, TitleBarStyle, WebviewUrl, WebviewWindow,
    WebviewWindowBuilder, WindowEvent,
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

const WINDOW: &str = "main";
const TRAY: &str = "main";
const DOWN_PAGE: &str = "down.html";
const POLL: Duration = Duration::from_secs(1);
const REQUEST_TIMEOUT: Duration = Duration::from_millis(800);
/// Polls in a row the daemon may leave unanswered before the window leaves the UI for the down page: one slow answer
/// of a busy daemon does not throw the open page away.
const MISSES_BEFORE_DOWN: u32 = 3;
const DEFAULT_HOST: &str = "127.0.0.1";
const DEFAULT_PORT: u16 = 8765;
/// What the daemon's proof puts before the nonce (`daemon_proof` in `dotagent.server.guard`).
const PROOF_LABEL: &[u8] = b"dot daemon proof\n";
const NONCE_BYTES: usize = 32;
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

/// What a poll sees at the daemon's address.
enum Seen {
    /// The daemon: it proved that it holds the token, then gave the numbers of the menu bar item.
    Counted(Count),
    /// Nothing answers in time, or the daemon has not made the token yet.
    Silent,
    /// Something answers without the proof the token gives (another program on the port, a daemon of an older version
    /// or of another home): it is sent nothing.
    Unproven,
}

/// Where the daemon listens and the token that signs the shell in; `token` is `None` until the daemon has created it.
#[derive(Clone)]
struct Daemon {
    base: String,
    token: Option<String>,
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

fn daemon() -> Daemon {
    let home = dot_home();
    let config: ConfigFile = fs::read_to_string(home.join("config.toml"))
        .ok()
        .and_then(|text| toml::from_str(&text).ok())
        .unwrap_or_default();
    let server = config.server.unwrap_or_default();
    let host = server.host.unwrap_or_else(|| DEFAULT_HOST.to_string());
    let port = server.port.unwrap_or(DEFAULT_PORT);
    let token = fs::read_to_string(home.join("api-token"))
        .ok()
        .map(|text| text.trim().to_string())
        .filter(|text| !text.is_empty());
    // An IPv6 host goes in brackets, as `dotagent.server.links.base_url` writes it.
    let bracketed = if host.contains(':') {
        format!("[{host}]")
    } else {
        host
    };
    Daemon {
        base: format!("http://{bracketed}:{port}"),
        token,
    }
}

/// The address that opens the UI signed in: the token travels in the fragment, which is never sent.
fn ui_url(daemon: &Daemon, token: &str) -> String {
    format!("{}/#token={token}", daemon.base)
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
        .timeout(REQUEST_TIMEOUT)
        .call()
    {
        Ok(response) => Some(
            response
                .into_json::<Proof>()
                .is_ok_and(|answer| proves(token, &nonce, &answer.proof)),
        ),
        Err(ureq::Error::Status(..)) => Some(false),
        Err(ureq::Error::Transport(_)) => None,
    }
}

/// The numbers of the menu bar item, or `None` while the daemon does not answer.
fn count(daemon: &Daemon, token: &str) -> Option<Count> {
    ureq::get(&format!("{}/api/activity/count", daemon.base))
        .set("Authorization", &format!("Bearer {token}"))
        .timeout(REQUEST_TIMEOUT)
        .call()
        .ok()?
        .into_json()
        .ok()
}

/// One look at the daemon: the count is asked, with the token, only of a daemon that has just proven it holds it.
fn look(daemon: &Daemon) -> Seen {
    let Some(token) = daemon.token.as_deref() else {
        return Seen::Silent;
    };
    match proven(daemon, token) {
        None => Seen::Silent,
        Some(false) => Seen::Unproven,
        Some(true) => count(daemon, token).map_or(Seen::Silent, Seen::Counted),
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

/// Bring the window up on the new chat page.
fn new_chat(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(WINDOW) {
        let _ = window.eval(
            "location.hash = '#/runs'; if (window.focusComposer) { window.focusComposer(); }",
        );
    }
    show(app);
}

/// Where the window should be: the UI when the daemon has just proven itself (`look`), the down page otherwise.
fn navigate(app: &AppHandle, up: bool, daemon: &Daemon) {
    let Some(window) = app.get_webview_window(WINDOW) else {
        return;
    };
    let target = match (up, &daemon.token) {
        (true, Some(token)) => ui_url(daemon, token),
        _ => format!("tauri://localhost/{DOWN_PAGE}"),
    };
    if let Ok(url) = url::Url::parse(&target) {
        let _ = window.navigate(url);
    }
}

/// Every second: the count for the menu bar item, and the window's page when the daemon comes or goes. `showing_ui`
/// is the page `setup` opened: the window leaves the UI after `MISSES_BEFORE_DOWN` unanswered polls in a row, and
/// opens it again at the first answer after that.
fn watch(app: AppHandle, showing_ui: bool) {
    thread::spawn(move || {
        let mut showing_ui = showing_ui;
        let mut misses: u32 = 0;
        loop {
            let daemon = daemon();
            let seen = look(&daemon);
            let counted = matches!(seen, Seen::Counted(_));
            misses = if counted { 0 } else { misses.saturating_add(1) };
            if let Some(tray) = app.tray_by_id(TRAY) {
                let (state, waiting, working) = match &seen {
                    Seen::Counted(count) => (count.state.as_str(), count.waiting, count.working),
                    Seen::Silent | Seen::Unproven => ("idle", 0, 0),
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
                    Seen::Unproven => {
                        "Dot · something else answers at the daemon's address".to_string()
                    }
                };
                let _ = tray.set_tooltip(Some(tip));
            }
            if showing_ui && misses >= MISSES_BEFORE_DOWN {
                navigate(&app, false, &daemon);
                showing_ui = false;
            } else if !showing_ui && counted {
                navigate(&app, true, &daemon);
                showing_ui = true;
            }
            thread::sleep(POLL);
        }
    });
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
    let separator = PredefinedMenuItem::separator(app)?;
    let quit = PredefinedMenuItem::quit(app, Some("Quit Dot"))?;
    let menu = if shortcut_taken {
        Menu::with_items(app, &[&open, &chat, &taken, &separator, &quit])?
    } else {
        Menu::with_items(app, &[&open, &chat, &separator, &quit])?
    };
    TrayIconBuilder::with_id(TRAY)
        .icon(tray_icon("idle"))
        .tooltip("Dot")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "open" => show(app),
            "chat" => new_chat(app),
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
        .setup(|app| {
            let handle = app.handle().clone();
            let daemon = daemon();
            let first = match &daemon.token {
                Some(token) if matches!(look(&daemon), Seen::Counted(_)) => {
                    WebviewUrl::External(url::Url::parse(&ui_url(&daemon, token))?)
                }
                _ => WebviewUrl::App(DOWN_PAGE.into()),
            };
            let showing_ui = matches!(first, WebviewUrl::External(_));
            window(&handle, first)?;
            // Another app may hold the shortcut: the shell then goes on without it, and its menu says so.
            let shortcut_taken = match shortcut(&handle) {
                Ok(()) => false,
                Err(error) => {
                    eprintln!("dot-shell: the shortcut ⇧⌘Space could not be registered: {error}");
                    true
                }
            };
            tray(&handle, shortcut_taken)?;
            watch(handle, showing_ui);
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
    use std::sync::mpsc::{self, Receiver};

    const TOKEN: &str = "MARKER-0123456789abcdef";
    const NONCE: &str = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
    /// `daemon_proof(TOKEN, NONCE)` of `dotagent.server.guard`; tests/unit/test_server_guard.py holds the same pair.
    const PROOF: &str = "4aea8baff569980b8b9dceb16e4d02f89d6563a252c47dcf94912b82ae440020";
    const COUNT: &str = r#"{"waiting": 1, "working": 0, "state": "waiting"}"#;

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
            token: Some(TOKEN.to_string()),
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
            assert!(matches!(look(&daemon), Seen::Unproven));
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
            token: Some(TOKEN.to_string()),
        };
        assert!(matches!(look(&daemon), Seen::Silent));
    }
}
