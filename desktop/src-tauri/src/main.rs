// Prevents an extra console window on Windows in release.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    fs,
    path::PathBuf,
    sync::Mutex,
    thread,
    time::Duration,
};

use serde::Deserialize;
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager, RunEvent, WebviewUrl, WebviewWindowBuilder,
};
use tauri_plugin_shell::{process::CommandChild, ShellExt};

#[derive(Deserialize)]
struct Runtime {
    #[allow(dead_code)]
    port: u16,
    token: String,
    api_base: String,
}

/// Holds the backend sidecar process so we can terminate it on app exit.
struct SidecarHandle(Mutex<Option<CommandChild>>);

fn runtime_path() -> PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
    PathBuf::from(home).join(".prepare_offline").join("runtime.json")
}

/// Wait for the backend sidecar to write its port+token handshake.
fn read_runtime(timeout_secs: u64) -> Option<Runtime> {
    let path = runtime_path();
    for _ in 0..(timeout_secs * 5) {
        if let Ok(bytes) = fs::read(&path) {
            if let Ok(rt) = serde_json::from_slice::<Runtime>(&bytes) {
                return Some(rt);
            }
        }
        thread::sleep(Duration::from_millis(200));
    }
    None
}

fn main() {
    // Remove any stale handshake so we don't read a previous run's port.
    let _ = fs::remove_file(runtime_path());

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .manage(SidecarHandle(Mutex::new(None)))
        .setup(|app| {
            // Start the Python backend as a sidecar on an OS-assigned free port.
            match app.shell().sidecar("pfo-backend") {
                Ok(cmd) => {
                    let cmd = cmd
                        .env("PREPARE_OFFLINE_PORT", "0")
                        .env("PREPARE_OFFLINE_DEV", "0");
                    match cmd.spawn() {
                        Ok((_rx, child)) => {
                            let state = app.state::<SidecarHandle>();
                            *state.0.lock().unwrap() = Some(child);
                        }
                        Err(e) => eprintln!("[pfo] failed to spawn sidecar: {e}"),
                    }
                }
                Err(e) => eprintln!("[pfo] sidecar not found: {e}"),
            }

            // Read the handshake and inject the API base + token BEFORE the
            // frontend loads. If it never arrives we still open the window
            // (the UI will simply show a connection error rather than crash).
            let init = match read_runtime(30) {
                Some(rt) => format!(
                    "window.__API_BASE__ = {:?}; window.__APP_TOKEN__ = {:?};",
                    rt.api_base, rt.token
                ),
                None => {
                    eprintln!("[pfo] backend did not report runtime in time");
                    String::new()
                }
            };

            match WebviewWindowBuilder::new(app, "main", WebviewUrl::default())
                .title("Prepare for Offline")
                .inner_size(460.0, 640.0)
                .initialization_script(&init)
                .build()
            {
                Ok(window) => {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
                Err(e) => eprintln!("[pfo] failed to build window: {e}"),
            }

            // Menu-bar (tray) icon. All fallible; never panic on failure.
            if let (Ok(show), Ok(quit)) = (
                MenuItem::with_id(app, "show", "Open", true, None::<&str>),
                MenuItem::with_id(app, "quit", "Quit", true, None::<&str>),
            ) {
                if let Ok(menu) = Menu::with_items(app, &[&show, &quit]) {
                    let mut builder = TrayIconBuilder::new().menu(&menu);
                    if let Some(icon) = app.default_window_icon().cloned() {
                        builder = builder.icon(icon);
                    }
                    let _ = builder
                        .on_menu_event(|app, event| match event.id().as_ref() {
                            "show" => {
                                if let Some(w) = app.get_webview_window("main") {
                                    let _ = w.show();
                                    let _ = w.set_focus();
                                }
                            }
                            "quit" => app.exit(0),
                            _ => {}
                        })
                        .build(app);
                }
            }

            Ok(())
        })
        .build(tauri::generate_context!());

    match app {
        Ok(app) => app.run(|app_handle, event| {
            if let RunEvent::ExitRequested { .. } = event {
                // Terminate the backend sidecar so it does not outlive the app.
                if let Some(state) = app_handle.try_state::<SidecarHandle>() {
                    if let Some(child) = state.0.lock().unwrap().take() {
                        let _ = child.kill();
                    }
                }
            }
        }),
        Err(e) => eprintln!("[pfo] error while starting Prepare for Offline: {e}"),
    }
}
