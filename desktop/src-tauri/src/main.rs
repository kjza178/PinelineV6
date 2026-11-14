// Pineline V6 desktop shell.
// - Tự spawn backend `python -m app --instance <tên>` (cổng đọc từ instances/<tên>.json).
// - Chờ cổng mở rồi chuyển cửa sổ sang http://127.0.0.1:<port><trang> (UI do FastAPI phục vụ, cùng origin → không CORS).
//   <trang> mặc định /v6/ = UI desktop (desktop/ui): 1 kết nối SSE, không polling, có thao tác job + hàng chờ
//   (2026-11-14). Đổi trang: env PINELINE_DESKTOP_PATH hoặc khoá "desktop_path" trong instances/<tên>.json
//   (vd "/ui3" = Studio cũ).
// - Đóng app → kill backend VÀ MỌI tiến trình con của nó (render worker, ffmpeg, engine ASR/TTS) — R2.1.
//   Chỉ backend do app tự spawn; backend đã chạy sẵn thì dùng lại, không kill.
// - Backend chết trong lúc khởi động → báo ngay trên màn chờ kèm đường dẫn log (R2.2), không chờ hết 180 s.
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

/// Trang mở sau khi backend lên: env PINELINE_DESKTOP_PATH → instances/<tên>.json "desktop_path" → /v6/.
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
    if p.starts_with('/') && !p.starts_with("//") { p.to_string() } else { "/v6/".to_string() }
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

/// R2.1: giết backend cùng cả cây tiến trình con.
/// `Child::kill()` trên Windows = TerminateProcess CHỈ tiến trình python: render worker / ffmpeg / engine ASR-TTS
/// (cháu) sống tiếp, giữ GPU và vẫn ghi file sau khi app đã đóng. `taskkill /T /F` giết cả cây; phải gọi TRƯỚC
/// khi cha chết (cha chết rồi thì không lần ra cháu). Hệ khác: chỉ kill tiến trình cha (app chỉ phát hành cho Windows).
fn kill_tree(child: &mut Child) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        let _ = Command::new("taskkill")
            .args(["/PID", &child.id().to_string(), "/T", "/F"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .creation_flags(0x0800_0000) // CREATE_NO_WINDOW
            .status();
    }
    let _ = child.kill();
    let _ = child.wait();
}

/// Chuỗi JS an toàn (chèn vào `win.eval`).
fn js_str(s: &str) -> String {
    serde_json::to_string(s).unwrap_or_else(|_| "\"\"".into())
}

fn main() {
    let root = find_root();
    let _ = std::fs::create_dir_all(root.join("scratch"));
    let instance = std::env::var("PINELINE_INSTANCE").unwrap_or_else(|_| "v6".into());
    let port = instance_port(&root, &instance);
    let page = desktop_path(&root, &instance);

    let log_path = root.join("scratch").join("desktop_backend.log").display().to_string();
    let mut spawn_err: Option<String> = None;
    let child = if port_open(port) {
        None // backend đã chạy (vd chạy tay để debug) → dùng lại
    } else {
        match spawn_backend(&root, &instance) {
            Ok(c) => Some(c),
            Err(e) => {
                eprintln!("không spawn được backend: {e}");
                spawn_err = Some(format!("Không chạy được Python ({e}). Kiểm tra Python trong PATH hoặc biến PINELINE_PYTHON."));
                None
            }
        }
    };

    let app = tauri::Builder::default()
        .manage(Backend(Mutex::new(child)))
        .setup(move |app| {
            let win = app.get_webview_window("main").expect("main window");
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                // Báo lỗi lên màn chờ: state = dead | timeout, kèm lý do + đường dẫn log.
                let fail = |state: &str, why: &str| {
                    let _ = win.eval(&format!(
                        "document.body.dataset.state={};var e=document.getElementById('why');if(e)e.textContent={};var l=document.getElementById('log');if(l)l.textContent={};",
                        js_str(state), js_str(why), js_str(&log_path)
                    ));
                };
                if let Some(why) = spawn_err {
                    std::thread::sleep(Duration::from_millis(600)); // chờ trang chờ nạp xong
                    fail("dead", &why);
                    return;
                }
                let t0 = Instant::now();
                while !port_open(port) {
                    // R2.2: backend đã thoát (lỗi import, cổng/khoá bị chiếm, thiếu gói…) → báo ngay.
                    let exited = handle
                        .state::<Backend>()
                        .0
                        .lock()
                        .ok()
                        .and_then(|mut g| g.as_mut().and_then(|c| c.try_wait().ok().flatten()));
                    if let Some(status) = exited {
                        fail("dead", &format!("Backend đã thoát ngay khi khởi động ({status})."));
                        return;
                    }
                    // F3: backend nạp cả server.py (engine, route V5) → khởi động lâu hơn F0.
                    if t0.elapsed() > Duration::from_secs(180) {
                        fail("timeout", "Backend không lên sau 180 s.");
                        return;
                    }
                    std::thread::sleep(Duration::from_millis(400));
                }
                // Cùng cổng phục vụ UI desktop /v6/ lẫn các trang cũ (/ui3, /films, /hongguo) — mở trang đã chọn.
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
                kill_tree(&mut c);
            }
        }
    });
}
