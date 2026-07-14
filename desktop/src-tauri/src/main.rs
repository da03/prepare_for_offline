// Prevents an extra console window on Windows in release.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{fs, path::PathBuf, thread, time::Duration};

use serde::Deserialize;
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager, WebviewUrl, WebviewWindowBuilder,
};
use tauri_plugin_shell::ShellExt;

#[derive(Deserialize)]
struct Runtime {
    port: u16,
    token: String,
    api_base: String,
}

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

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            // Start the Python backend as a sidecar on an OS-assigned free port.
            let sidecar = app
                .shell()
                .sidecar("pfo-backend")
                .expect("pfo-backend sidecar not found")
                .env("PREPARE_OFFLINE_PORT", "0")
                .env("PREPARE_OFFLINE_DEV", "0");
            let (_rx, _child) = sidecar.spawn().expect("failed to start backend");

            let rt = read_runtime(30).expect("backend did not report its runtime in time");

            // Inject the API base + token before the frontend loads, so the
            // web UI talks to the loopback backend with the per-install token.
            let init = format!(
                "window.__API_BASE__ = {:?}; window.__APP_TOKEN__ = {:?};",
                rt.api_base, rt.token
            );

            let window = WebviewWindowBuilder::new(app, "main", WebviewUrl::default())
                .title("Prepare for Offline")
                .inner_size(460.0, 640.0)
                .initialization_script(&init)
                .build()?;
            window.show()?;

            // Menu-bar (tray) icon with show/quit.
            let show = MenuItem::with_id(app, "show", "Open", true, None::<&str>)?;
            let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show, &quit])?;
            let _tray = TrayIconBuilder::new()
                .icon(app.default_window_icon().unwrap().clone())
                .menu(&menu)
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
                .build(app)?;

            let _ = port_unused_warning(rt.port);
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running Prepare for Offline");
}

fn port_unused_warning(_port: u16) -> u16 {
    _port
}
