//! The desktop shell of Dot: a native window around the web UI the daemon serves, a menu bar item with the face of
//! the web UI and the count of what waits for the user, a global shortcut for a new chat, and notifications sent by
//! the web UI through the shell. The daemon stays owned by launchd (`dotx service install`): the shell connects to it
//! with the token of the token file and never runs the agent itself. While the daemon is down the window shows the
//! page that says how to start it, and opens the UI by itself when the daemon answers.

use std::env;
use std::fs;
use std::path::PathBuf;
use std::thread;
use std::time::Duration;

use serde::Deserialize;
use tauri::image::Image;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{
    AppHandle, Manager, RunEvent, TitleBarStyle, WebviewUrl, WebviewWindow, WebviewWindowBuilder,
    WindowEvent,
};
use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut, ShortcutState};

const WINDOW: &str = "main";
const TRAY: &str = "main";
const DOWN_PAGE: &str = "down.html";
const POLL: Duration = Duration::from_secs(1);
const REQUEST_TIMEOUT: Duration = Duration::from_millis(800);
const DEFAULT_HOST: &str = "127.0.0.1";
const DEFAULT_PORT: u16 = 8765;
const ICON_IDLE: &[u8] = include_bytes!("../icons/tray-idle.png");
const ICON_WORKING: &[u8] = include_bytes!("../icons/tray-working.png");
const ICON_WAITING: &[u8] = include_bytes!("../icons/tray-waiting.png");

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
    let bracketed = if host.contains(':') { format!("[{host}]") } else { host };
    Daemon {
        base: format!("http://{bracketed}:{port}"),
        token,
    }
}

/// The address that opens the UI signed in: the token travels in the fragment, which is never sent.
fn ui_url(daemon: &Daemon, token: &str) -> String {
    format!("{}/#token={token}", daemon.base)
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
/// the web UI's own top bars: the system draws only the traffic lights over the page, the page keeps room for them
/// (`data-shell="desktop"`, set by the UI when it runs in the shell) and drags the window by its bars. The shell's
/// own drag-and-drop handler is off so the composer's HTML drop works.
fn window(app: &AppHandle, url: WebviewUrl) -> tauri::Result<WebviewWindow> {
    if let Some(existing) = app.get_webview_window(WINDOW) {
        return Ok(existing);
    }
    WebviewWindowBuilder::new(app, WINDOW, url)
        .title("Dot")
        .title_bar_style(TitleBarStyle::Overlay)
        .hidden_title(true)
        .inner_size(1280.0, 860.0)
        .min_inner_size(390.0, 600.0)
        .disable_drag_drop_handler()
        .build()
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

/// Where the window should be: the UI when the daemon answers, the down page otherwise.
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

/// Every second: the count for the menu bar item, and the window's page when the daemon comes or goes.
fn watch(app: AppHandle) {
    thread::spawn(move || {
        let mut was_up: Option<bool> = None;
        loop {
            let daemon = daemon();
            let seen = daemon
                .token
                .as_deref()
                .and_then(|token| count(&daemon, token));
            let up = seen.is_some();
            if let Some(tray) = app.tray_by_id(TRAY) {
                let (state, waiting, working) = match &seen {
                    Some(count) => (count.state.as_str(), count.waiting, count.working),
                    None => ("idle", 0, 0),
                };
                let _ = tray.set_icon_with_as_template(Some(tray_icon(state)), false);
                let _ = tray.set_title(if waiting > 0 {
                    Some(waiting.to_string())
                } else {
                    None
                });
                let tip = match &seen {
                    Some(_) => format!("Dot · {waiting} waiting for you · {working} working"),
                    None => "Dot · the daemon is not running".to_string(),
                };
                let _ = tray.set_tooltip(Some(tip));
            }
            if was_up != Some(up) {
                navigate(&app, up, &daemon);
                was_up = Some(up);
            }
            thread::sleep(POLL);
        }
    });
}

fn tray(app: &AppHandle) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Open Dot", true, None::<&str>)?;
    let chat = MenuItem::with_id(app, "chat", "New chat", true, Some("CmdOrCtrl+Shift+Space"))?;
    let quit = PredefinedMenuItem::quit(app, Some("Quit Dot"))?;
    let menu = Menu::with_items(
        app,
        &[&open, &chat, &PredefinedMenuItem::separator(app)?, &quit],
    )?;
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
                Some(token) if count(&daemon, token).is_some() => {
                    WebviewUrl::External(url::Url::parse(&ui_url(&daemon, token))?)
                }
                _ => WebviewUrl::App(DOWN_PAGE.into()),
            };
            window(&handle, first)?;
            tray(&handle)?;
            shortcut(&handle)?;
            watch(handle);
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
