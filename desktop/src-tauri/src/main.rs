// Pineline V6 desktop shell.
// - Tự spawn backend `python -m app --instance <tên>` (cổng đọc từ instances/<tên>.json).
// - Chờ cổng mở rồi chuyển cửa sổ sang http://127.0.0.1:<port><trang> (UI do FastAPI phục vụ, cùng origin → không CORS).
//   <trang> mặc định /ui3 = UI đầy đủ chức năng (tạo/chạy/dừng job, hàng chờ, HOLD…). Trang /v6/ (desktop/ui) mới
//   chỉ là bảng XEM, chưa có thao tác → không còn là mặc định (fix 2026-11-14). Đổi: env PINELINE_DESKTOP_PATH
//   hoặc khoá "desktop_path" trong instances/<tên>.json (vd "/v6/").
// - Đóng app → kill backend (chỉ backend do app tự spawn; nếu backend đã chạy sẵn thì dùng lại, không kill).
// UI không polling: 1 kết nối SSE /api/events thay cho setInterval 2 s gọi /api/jobs (nguyên nhân treo cũ).
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::net::{SocketAddr, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent, Url};

struct Backend(Mutex<Option<Child>>);

fn find_root() -> PathBuf {
    if let Ok(r) = std::env::var("PINELINE_ROOT") {
        return PathBuf::from(r);
    }
    let mut starts: Vec<PathBuf> = vec![];
    if let Ok(c) = std::env::current_dir() { starts.push(c); }
    if let Ok(e) = std::env::current_exe() { if let Some(p) = e.parent() { starts.push(p.to_path_buf()); } }
    for s in starts {
        let mut cur: Option<&Path> = Some(s.as_path());
        while let Some(d) = cur {
            if d.join("instances").is_dir() && d.join("app").is_dir() {
                return d.to_path_buf();
            }
            cur = d.parent();
        }
    }
    std::env::current_dir().unwrap_or_default()
}

fn instance_port(root: &Path, name: &str) -> u16 {
    let p = root.join("instances").join(format!("{name}.json"));
    std::fs::read_to_string(p)
        .ok()
        .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
        .and_then(|v| v.get("port").and_then(|x| x.as_u64()))
        .map(|x| x as u16)
        .unwrap_or(8806)
}

/// Trang mở sau khi backend lên: env PINELINE_DESKTOP_PATH → instances/<tên>.json "desktop_path" → /ui3.
/// Chỉ nhận đường dẫn nội bộ bắt đầu bằng "/" (không nhận URL ngoài).
fn desktop_path(root: &Path, name: &str) -> String {
    let from_json = || {
        std::fs::read_to_string(root.join("instances").join(format!("{name}.json")))
            .ok()
            .and_then(|t| serde_json::from_str::<serde_json::Value>(&t).ok())
            .and_then(|v| v.get("desktop_path").and_then(|x| x.as_str()).map(|s| s.to_string()))
    };
    let p = std::env::var("PINELINE_DESKTOP_PATH").ok().or_else(from_json).unwrap_or_default();
    let p = p.trim();
    if p.starts_with('/') && !p.starts_with("//") { p.to_string() } else { "/ui3".to_string() }
}

fn port_open(port: u16) -> bool {
    let addr: SocketAddr = ([127, 0, 0, 1], port).into();
    TcpStream::connect_timeout(&addr, Duration::from_millis(300)).is_ok()
}

fn spawn_backend(root: &Path, instance: &str) -> std::io::Result<Child> {
    let py = std::env::var("PINELINE_PYTHON").unwrap_or_else(|_| "python".into());
    let log = std::fs::File::create(root.join("scratch").join("desktop_backend.log"))
        .or_else(|_| std::fs::File::create(std::env::temp_dir().join("pineline_v6_backend.log")))?;
    let mut cmd = Command::new(py);
    cmd.args(["-X", "utf8", "-m", "app", "--instance", instance])
        .current_dir(root)
        .env("PYTHONIOENCODING", "utf-8")
        .stdin(Stdio::null())
        .stdout(Stdio::from(log.try_clone()?))
        .stderr(Stdio::from(log));
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
    }
    cmd.spawn()
}

fn main() {
    let root = find_root();
    let _ = std::fs::create_dir_all(root.join("scratch"));
    let instance = std::env::var("PINELINE_INSTANCE").unwrap_or_else(|_| "v6".into());
    let port = instance_port(&root, &instance);
    let page = desktop_path(&root, &instance);

    let child = if port_open(port) {
        None // backend đã chạy (vd chạy tay để debug) → dùng lại
    } else {
        match spawn_backend(&root, &instance) {
            Ok(c) => Some(c),
            Err(e) => {
                eprintln!("không spawn được backend: {e}");
                None
            }
        }
    };

    let app = tauri::Builder::default()
        .manage(Backend(Mutex::new(child)))
        .setup(move |app| {
            let win = app.get_webview_window("main").expect("main window");
            std::thread::spawn(move || {
                let t0 = Instant::now();
                while !port_open(port) {
                    // F3: backend nạp cả server.py (engine, route V5) → khởi động lâu hơn F0.
                    if t0.elapsed() > Duration::from_secs(180) {
                        let _ = win.eval("document.body.dataset.state='timeout'");
                        return;
                    }
                    std::thread::sleep(Duration::from_millis(400));
                }
                // Cùng cổng phục vụ UI đầy đủ (/ui3, /films, /hongguo) lẫn bảng xem /v6/ — mở trang đã chọn.
                if let Ok(url) = Url::parse(&format!("http://127.0.0.1:{port}{page}")) {
                    let _ = win.navigate(url);
                }
            });
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("tauri build");

    app.run(|handle, ev| {
        if let RunEvent::Exit = ev {
            if let Some(mut c) = handle.state::<Backend>().0.lock().unwrap().take() {
                let _ = c.kill();
                let _ = c.wait();
            }
        }
    });
}
