use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

use tauri::{Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};

/// Holds the Python sidecar child so it can be killed when the app exits.
struct SidecarGuard(Mutex<Option<Child>>);

/// Spawn the Python sidecar and read the ephemeral port it prints as
/// `PORT=<n>` on stdout. Dev launches it from source via `uv run`; the
/// packaged app replaces this with the bundled sidecar binary (later task).
/// Returns `(child, port)`, or `None` if it could not be spawned.
fn spawn_sidecar() -> Option<(Child, u16)> {
    let sidecar_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../sidecar");
    let mut child = Command::new("uv")
        .args(["run", "python", "-m", "entropy_sidecar"])
        .current_dir(&sidecar_dir)
        .stdout(Stdio::piped())
        .spawn()
        .ok()?;
    let stdout = child.stdout.take()?;
    let mut reader = BufReader::new(stdout);
    let mut port: u16 = 0;
    let mut line = String::new();
    loop {
        line.clear();
        match reader.read_line(&mut line) {
            Ok(0) => break,
            Ok(_) => {
                if let Some(rest) = line.trim().strip_prefix("PORT=") {
                    port = rest.parse().unwrap_or(0);
                    break;
                }
            }
            Err(_) => break,
        }
    }
    // Keep draining stdout on a background thread so the pipe never fills and
    // blocks the sidecar once it starts logging.
    std::thread::spawn(move || {
        let mut sink = String::new();
        while reader.read_line(&mut sink).unwrap_or(0) > 0 {
            sink.clear();
        }
    });
    Some((child, port))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            let port = match spawn_sidecar() {
                Some((child, port)) => {
                    app.manage(SidecarGuard(Mutex::new(Some(child))));
                    port
                }
                None => {
                    app.manage(SidecarGuard(Mutex::new(None)));
                    0
                }
            };
            // Inject the sidecar port BEFORE the frontend's own scripts run, so
            // App.tsx's resolvePort() picks it up from window.__SIDECAR_PORT__.
            let init = format!("window.__SIDECAR_PORT__ = {};", port);
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App("index.html".into()))
                .title("Entropy")
                .inner_size(1280.0, 820.0)
                .min_inner_size(1000.0, 680.0)
                .initialization_script(&init)
                .build()?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            if let RunEvent::Exit = event {
                if let Some(guard) = app.try_state::<SidecarGuard>() {
                    if let Some(mut child) = guard.0.lock().unwrap().take() {
                        let _ = child.kill();
                    }
                }
            }
        });
}
