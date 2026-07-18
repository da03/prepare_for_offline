// Prevents an extra console window on Windows in release.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{fs, path::PathBuf, process::Command as StdCommand, sync::Mutex, thread, time::Duration};

use serde::Deserialize;
use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    Manager, RunEvent, WebviewUrl, WebviewWindowBuilder,
};
use tauri_plugin_shell::{
    process::{CommandChild, CommandEvent},
    ShellExt,
};

const TRAY_ICON: tauri::image::Image<'_> = tauri::include_image!("icons/tray-template@2x.png");

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
    if let Ok(home) = std::env::var("PREPARE_OFFLINE_HOME") {
        return PathBuf::from(home).join("runtime.json");
    }
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".into());
    PathBuf::from(home)
        .join(".prepare_offline")
        .join("runtime.json")
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

fn terminate_sidecar(app_handle: &tauri::AppHandle) {
    let Some(state) = app_handle.try_state::<SidecarHandle>() else {
        return;
    };
    let Ok(mut guard) = state.0.lock() else {
        return;
    };
    let Some(child) = guard.take() else {
        return;
    };
    let pid = child.pid().to_string();
    // PyInstaller's one-file bootloader spawns the actual Python process.
    // Terminate that descendant before killing the bootloader parent.
    #[cfg(target_family = "unix")]
    {
        let _ = StdCommand::new("pkill")
            .args(["-TERM", "-P", &pid])
            .status();
    }
    let _ = child.kill();
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
                    let mut cmd = cmd
                        .env("PREPARE_OFFLINE_PORT", "0")
                        .env("PREPARE_OFFLINE_DEV", "0");
                    if let Some(public_key) = option_env!("PFO_KNOWLEDGE_PUBLIC_KEY") {
                        if !public_key.is_empty() {
                            cmd = cmd.env("PFO_KNOWLEDGE_PUBLIC_KEY", public_key);
                        }
                    }
                    if let Ok(resources) = app.path().resource_dir() {
                        let model = resources.join("resources").join("qwen3-0.6b-q6_k.gguf");
                        if model.is_file() {
                            cmd = cmd.env(
                                "PREPARE_OFFLINE_MODEL_PATH",
                                model.to_string_lossy().to_string(),
                            );
                        }
                        let paw_programs = resources.join("resources").join("paw-programs");
                        if paw_programs.is_dir() {
                            cmd = cmd.env(
                                "PREPARE_OFFLINE_PAW_PROGRAMS_PATH",
                                paw_programs.to_string_lossy().to_string(),
                            );
                        }
                    }
                    match cmd.spawn() {
                        Ok((mut rx, child)) => {
                            let state = app.state::<SidecarHandle>();
                            *state.0.lock().unwrap() = Some(child);
                            tauri::async_runtime::spawn(async move {
                                while let Some(event) = rx.recv().await {
                                    match event {
                                        CommandEvent::Stdout(line) => {
                                            eprintln!(
                                                "[pfo-backend] {}",
                                                String::from_utf8_lossy(&line)
                                            );
                                        }
                                        CommandEvent::Stderr(line) => {
                                            eprintln!(
                                                "[pfo-backend:error] {}",
                                                String::from_utf8_lossy(&line)
                                            );
                                        }
                                        _ => {}
                                    }
                                }
                            });
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
                    let _ = TrayIconBuilder::new()
                        .menu(&menu)
                        .icon(TRAY_ICON.clone())
                        .icon_as_template(true)
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
            if matches!(event, RunEvent::ExitRequested { .. } | RunEvent::Exit) {
                terminate_sidecar(app_handle);
            }
        }),
        Err(e) => eprintln!("[pfo] error while starting Prepare for Offline: {e}"),
    }
}
