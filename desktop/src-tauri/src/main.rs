//! 桌面壳：拉起 Python sidecar（本地 HTTP 后端），把端口与一次性 token 交给前端。
//!
//! 设计要点：
//! - 不依赖 tauri-plugin-shell：直接用 std::process，少一层权限配置与 CI 变量。
//! - 不阻塞 UI：端口通过后台线程读 stdout 写入共享槽位，前端轮询 `backend_info`。
//! - 便携优先：sidecar 与主程序同目录（Windows 便携 zip / macOS .app 的 Contents/MacOS）。
//! - 退出时显式 kill 子进程，避免 macOS 上留下孤儿进程。
#![cfg_attr(all(not(debug_assertions), target_os = "windows"), windows_subsystem = "windows")]

use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::Serialize;
use tauri::{Manager, RunEvent, State};

const SIDECAR_STEM: &str = "novel-rag-sidecar";

struct Backend {
    child: Mutex<Option<Child>>,
    port: Arc<Mutex<Option<u16>>>,
    token: String,
    home: PathBuf,
    mode: &'static str,
}

#[derive(Serialize, Clone)]
struct BackendInfo {
    ready: bool,
    port: Option<u16>,
    token: String,
    mode: &'static str,
    home: String,
}

#[tauri::command]
fn backend_info(state: State<'_, Backend>) -> BackendInfo {
    let port = *state.port.lock().unwrap();
    BackendInfo {
        ready: port.is_some(),
        port,
        token: state.token.clone(),
        mode: state.mode,
        home: state.home.to_string_lossy().to_string(),
    }
}

fn random_token() -> String {
    // 不引入 rand：时间戳 + pid 足够做本地一次性 token
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let pid = std::process::id() as u128;
    let mut x = nanos ^ (pid << 64);
    let mut out = String::with_capacity(32);
    for _ in 0..4 {
        x = x.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        out.push_str(&format!("{:08x}", (x >> 32) as u32));
    }
    out
}

fn sidecar_binary() -> Option<PathBuf> {
    let name = if cfg!(windows) {
        format!("{SIDECAR_STEM}.exe")
    } else {
        SIDECAR_STEM.to_string()
    };
    let dir = std::env::current_exe().ok()?.parent()?.to_path_buf();
    let candidate = dir.join(name);
    candidate.exists().then_some(candidate)
}

/// 开发模式回退：直接用源码跑 `python -m novel_rag`（CI 打包时不会走到这里）。
fn dev_backend_dir() -> Option<PathBuf> {
    let mut dir = std::env::current_exe().ok()?;
    for _ in 0..6 {
        dir = dir.parent()?.to_path_buf();
        let backend = dir.join("backend").join("novel_rag");
        if backend.is_dir() {
            return Some(dir.join("backend"));
        }
    }
    None
}

fn spawn_sidecar(app: &tauri::AppHandle, token: &str) -> (Option<Child>, Arc<Mutex<Option<u16>>>, &'static str, PathBuf) {
    let port_slot: Arc<Mutex<Option<u16>>> = Arc::new(Mutex::new(None));
    let home = app
        .path()
        .app_data_dir()
        .unwrap_or_else(|_| PathBuf::from("."));
    let _ = std::fs::create_dir_all(&home);

    let mut mode = "sidecar";
    let mut command = match sidecar_binary() {
        Some(path) => Command::new(path),
        None => {
            mode = "python-dev";
            let Some(backend) = dev_backend_dir() else {
                eprintln!("[novel-rag] sidecar 与 backend 源码都没找到");
                return (None, port_slot, "missing", home);
            };
            let mut cmd = Command::new(if cfg!(windows) { "python" } else { "python3" });
            cmd.arg("-m").arg("novel_rag").current_dir(backend);
            cmd
        }
    };

    command
        .env("NOVEL_RAG_TOKEN", token)
        .env("NOVEL_RAG_HOME", &home)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }

    let mut child = match command.spawn() {
        Ok(child) => child,
        Err(err) => {
            eprintln!("[novel-rag] 启动 sidecar 失败: {err}");
            return (None, port_slot, mode, home);
        }
    };

    if let Some(stdout) = child.stdout.take() {
        let slot = port_slot.clone();
        thread::spawn(move || {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                if let Some(rest) = line.strip_prefix("PORT:") {
                    if let Ok(port) = rest.trim().parse::<u16>() {
                        *slot.lock().unwrap() = Some(port);
                    }
                }
                eprintln!("[sidecar] {line}");
            }
        });
    }
    if let Some(stderr) = child.stderr.take() {
        thread::spawn(move || {
            for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                eprintln!("[sidecar:err] {line}");
            }
        });
    }

    (Some(child), port_slot, mode, home)
}

fn main() {
    let token = random_token();

    tauri::Builder::default()
        .setup(move |app| {
            let handle = app.handle().clone();
            let (child, port, mode, home) = spawn_sidecar(&handle, &token);
            app.manage(Backend {
                child: Mutex::new(child),
                port,
                token: token.clone(),
                home,
                mode,
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![backend_info])
        .build(tauri::generate_context!())
        .expect("failed to build tauri application")
        .run(|app_handle, event| {
            if let RunEvent::Exit = event {
                if let Some(state) = app_handle.try_state::<Backend>() {
                    if let Some(mut child) = state.child.lock().unwrap().take() {
                        let _ = child.kill();
                        let _ = child.wait();
                    }
                }
            }
        });
}
