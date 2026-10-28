//! Tauri 薄壳：拉起 Python sidecar，把端口与一次性 token 交给前端。
//!
//! 三条硬性约束：
//! 1. 参数必须与 `novel_rag.__main__` 的 argparse 对齐（`--home` / `--port`）；
//!    早期漏了 `--home`，sidecar 立刻以 `unrecognized arguments` 退出，
//!    表现就是「本地后端启动超时」且没有任何日志。
//! 2. sidecar 的 stdout/stderr 必须落盘到 `<app_data>/logs/sidecar.log`——
//!    Python 自己的 backend.log 要等日志初始化后才存在，启动期崩溃只有这里能看。
//! 3. 不能阻塞 UI：端口由后台线程写入共享槽位，前端轮询 `backend_info`。

#![cfg_attr(all(not(debug_assertions), target_os = "windows"), windows_subsystem = "windows")]

use std::fs::{self, File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::Serialize;
use tauri::{Manager, RunEvent, State};

/// 开发模式下 sidecar 是一个 Python 脚本，需要显式用解释器启动。
const DEV_MARKER: &str = "novel_rag";

struct Backend {
    child: Mutex<Child>,
    port: Arc<Mutex<Option<u16>>>,
    error: Mutex<Option<String>>,
    token: String,
    mode: &'static str,
    home: PathBuf,
    log: PathBuf,
}

#[derive(Serialize)]
struct BackendInfo {
    ready: bool,
    port: Option<u16>,
    token: String,
    mode: &'static str,
    home: String,
    log: String,
    error: Option<String>,
}

fn write_log(log: &Arc<Mutex<Option<File>>>, line: &str) {
    if let Ok(mut guard) = log.lock() {
        if let Some(file) = guard.as_mut() {
            let _ = writeln!(file, "{line}");
            let _ = file.flush();
        }
    }
}

fn stamp() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    format!("[{secs}]")
}

#[tauri::command]
fn backend_info(state: State<'_, Backend>) -> BackendInfo {
    let log = state.log.display().to_string();
    let home = state.home.display().to_string();
    let port = *state.port.lock().unwrap();

    if let Some(p) = port {
        return BackendInfo {
            ready: true,
            port: Some(p),
            token: state.token.clone(),
            mode: state.mode,
            home,
            log,
            error: None,
        };
    }

    // 端口还没出来：先确认子进程是不是已经死了，别让前端等到超时
    let mut message = state.error.lock().unwrap().clone();
    if message.is_none() {
        if let Ok(mut child) = state.child.lock() {
            if let Ok(Some(status)) = child.try_wait() {
                let detail = match status.code() {
                    Some(code) => format!("exit code {code}"),
                    None => "被信号终止".to_string(),
                };
                let text = format!("后端进程已退出（{detail}），详见日志 {log}");
                *state.error.lock().unwrap() = Some(text.clone());
                message = Some(text);
            }
        }
    }

    BackendInfo {
        ready: false,
        port: None,
        token: state.token.clone(),
        mode: state.mode,
        home,
        log,
        error: message,
    }
}

/// 便携版：sidecar 与主程序同目录；开发模式：向上找 `backend/novel_rag`。
fn resolve_sidecar() -> (Option<PathBuf>, Option<PathBuf>) {
    if let Ok(exe) = std::env::current_exe() {
        if let Some(dir) = exe.parent() {
            let candidate = dir.join(format!("novel-rag-sidecar{}", std::env::consts::EXE_SUFFIX));
            if candidate.exists() {
                return (Some(candidate), None);
            }
            let mut cursor = dir.to_path_buf();
            for _ in 0..6 {
                let pkg = cursor.join("backend").join(DEV_MARKER);
                if pkg.is_dir() {
                    return (None, Some(pkg));
                }
                if !cursor.pop() {
                    break;
                }
            }
        }
    }
    (None, None)
}

fn main() {
    tauri::Builder::default()
        .setup(move |app| {
            let handle = app.handle().clone();
            let home = handle
                .path()
                .app_data_dir()
                .unwrap_or_else(|_| PathBuf::from("."));
            let _ = fs::create_dir_all(&home);
            let _ = fs::create_dir_all(home.join("models"));

            let log_path = home.join("logs").join("sidecar.log");
            if let Some(parent) = log_path.parent() {
                let _ = fs::create_dir_all(parent);
            }
            let log: Arc<Mutex<Option<File>>> = Arc::new(Mutex::new(
                OpenOptions::new()
                    .create(true)
                    .append(true)
                    .open(&log_path)
                    .ok(),
            ));
            write_log(
                &log,
                &format!("{}\n===== sidecar start =====", stamp()),
            );
            write_log(&log, &format!("[app] home = {}", home.display()));
            write_log(&log, &format!("[app] log  = {}", log_path.display()));

            let token = generate_token();
            let (sidecar, dev_pkg) = resolve_sidecar();
            write_log(
                &log,
                &format!("[app] sidecar = {:?}, dev_pkg = {:?}", sidecar, dev_pkg),
            );

            let mut command = match (&sidecar, &dev_pkg) {
                (Some(path), _) => Command::new(path),
                (None, Some(pkg)) => {
                    // 开发模式：用当前解释器跑模块
                    let python = which_python();
                    write_log(&log, &format!("[app] dev python = {python}"));
                    let mut cmd = Command::new(python);
                    cmd.current_dir(pkg.parent().unwrap_or(Path::new(".")))
                        .arg("-m")
                        .arg(DEV_MARKER);
                    cmd
                }
                _ => {
                    let text = "找不到后端可执行文件（novel-rag-sidecar）。便携包请把 exe 与 sidecar 放在同一目录。"
                        .to_string();
                    write_log(&log, &format!("[app] FATAL {text}"));
                    let port: Arc<Mutex<Option<u16>>> = Arc::new(Mutex::new(None));
                    let placeholder = Command::new(if cfg!(windows) { "cmd" } else { "true" })
                        .arg(if cfg!(windows) { "/C" } else { "exit" })
                        .arg("0")
                        .stdout(Stdio::null())
                        .stderr(Stdio::null())
                        .spawn();
                    if let Ok(child) = placeholder {
                        app.manage(Backend {
                            child: Mutex::new(child),
                            port,
                            error: Mutex::new(Some(text)),
                            token,
                            mode: "missing",
                            home,
                            log: log_path,
                        });
                    }
                    return Ok(());
                }
            };

            command
                .arg("--home")
                .arg(&home)
                .arg("--port")
                .arg("0")
                .env("NOVEL_RAG_HOME", &home)
                .env("NOVEL_RAG_LOG_DIR", home.join("logs"))
                .env("NOVEL_RAG_TOKEN", &token)
                .stdin(Stdio::null())
                .stdout(Stdio::piped())
                .stderr(Stdio::piped());

            #[cfg(windows)]
            {
                use std::os::windows::process::CommandExt;
                const CREATE_NO_WINDOW: u32 = 0x0800_0000;
                command.creation_flags(CREATE_NO_WINDOW);
            }

            write_log(&log, &format!("[app] spawn: {:?}", command));
            let mut child = command.spawn()?;

            let port_slot: Arc<Mutex<Option<u16>>> = Arc::new(Mutex::new(None));

            if let Some(stdout) = child.stdout.take() {
                let slot = port_slot.clone();
                let sink = log.clone();
                thread::spawn(move || {
                    let reader = BufReader::new(stdout);
                    for line in reader.lines().map_while(Result::ok) {
                        write_log(&sink, &format!("[out] {line}"));
                        if let Some(rest) = line.strip_prefix("PORT:") {
                            if let Ok(port) = rest.trim().parse::<u16>() {
                                *slot.lock().unwrap() = Some(port);
                            }
                        }
                    }
                    write_log(&sink, "[out] <closed>");
                });
            }

            if let Some(stderr) = child.stderr.take() {
                let sink = log.clone();
                thread::spawn(move || {
                    let reader = BufReader::new(stderr);
                    for line in reader.lines().map_while(Result::ok) {
                        write_log(&sink, &format!("[err] {line}"));
                    }
                    write_log(&sink, "[err] <closed>");
                });
            }

            let mode: &'static str = if sidecar.is_some() {
                "sidecar"
            } else {
                "python-dev"
            };

            app.manage(Backend {
                child: Mutex::new(child),
                port: port_slot,
                error: Mutex::new(None),
                token,
                mode,
                home,
                log: log_path,
            });

            if dev_pkg.is_some() {
                write_log(&log, "[app] dev mode: python -m novel_rag");
            }

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![backend_info])
        .build(tauri::generate_context!())
        .expect("failed to build tauri app")
        .run(|app_handle, event| {
            if let RunEvent::Exit = event {
                if let Some(state) = app_handle.try_state::<Backend>() {
                    if let Ok(mut child) = state.child.lock() {
                        let _ = child.kill();
                        let _ = child.wait();
                    }
                }
            }
        });
}

fn which_python() -> String {
    for candidate in ["python3", "python"] {
        if Command::new(candidate)
            .arg("--version")
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map(|s| s.success())
            .unwrap_or(false)
        {
            return candidate.to_string();
        }
    }
    "python3".to_string()
}

/// 一次性 token：避免其它本机进程直接读写用户的知识库。
fn generate_token() -> String {
    use std::collections::hash_map::RandomState;
    use std::hash::{BuildHasher, Hasher};

    let mut out = String::with_capacity(48);
    for _ in 0..3 {
        let mut hasher = RandomState::new().build_hasher();
        hasher.write_u128(
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map(|d| d.as_nanos())
                .unwrap_or(0),
        );
        out.push_str(&format!("{:016x}", hasher.finish()));
    }
    out
}
