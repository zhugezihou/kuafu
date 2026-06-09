mod agent;

use agent::AgentManager;
use std::path::PathBuf;
use std::sync::Mutex;
use std::time::{SystemTime, UNIX_EPOCH};
use tauri::tray::{TrayIconBuilder, MouseButton, MouseButtonState, TrayIconEvent};
use tauri::menu::{Menu, MenuItem};
use tauri::Emitter;
use tauri::Manager;
use tauri::Runtime;

struct AppState {
    agent: Mutex<AgentManager>,
}

#[tauri::command]
fn start_agent(state: tauri::State<AppState>) -> Result<agent::AgentStatus, String> {
    state.agent.lock().map_err(|e| e.to_string())?.start()
}

#[tauri::command]
fn stop_agent(state: tauri::State<AppState>) -> Result<(), String> {
    state.agent.lock().map_err(|e| e.to_string())?.stop()
}

#[tauri::command]
fn agent_status(state: tauri::State<AppState>) -> agent::AgentStatus {
    state
        .agent
        .lock()
        .map(|mut a| a.status())
        .unwrap_or(agent::AgentStatus {
            running: false,
            pid: None,
            gateway_port: 8081,
            python_path: String::new(),
            error: Some("状态不可用".into()),
        })
}

#[tauri::command]
fn restart_agent(state: tauri::State<AppState>) -> Result<agent::AgentStatus, String> {
    state.agent.lock().map_err(|e| e.to_string())?.restart()
}

#[tauri::command]
fn update_agent_config(
    config: agent::AgentConfig,
    state: tauri::State<AppState>,
) -> Result<(), String> {
    state
        .agent
        .lock()
        .map_err(|e| e.to_string())?
        .update_config(config);
    Ok(())
}

#[tauri::command]
fn check_setup(state: tauri::State<AppState>) -> agent::SetupStatus {
    state.agent.lock().map(|a| a.check_setup())
        .unwrap_or(agent::SetupStatus {
            python_found: false, pyyaml_installed: false,
            kuafu_found: false, gateway_running: false,
            python_path: String::new(),
            error: Some("状态不可用".into()),
            setup_complete: false,
        })
}

/// 通过 Rust 发送 POST 请求到本地 Gateway（绕过 WebView CORS 限制）
/// 支持流式返回：通过 Tauri event 推送每段响应
#[tauri::command]
fn send_task(task: String, app_handle: tauri::AppHandle) -> Result<String, String> {
    use std::io::{Read, Write};
    use std::net::TcpStream;
    use std::time::Duration;
    use std::thread;

    let body = serde_json::json!({
        "task": task,
        "mode": "standard",
        "sync": true,
    });
    let body_str = serde_json::to_string(&body).map_err(|e| format!("序列化失败: {e}"))?;

    let mut stream = TcpStream::connect_timeout(
        &"127.0.0.1:8081".parse().unwrap(),
        Duration::from_secs(5),
    )
    .map_err(|e| format!("连接 Gateway 失败: {e}"))?;

    let request = format!(
        "POST /api/task HTTP/1.0\r\n\
         Host: localhost:8081\r\n\
         Content-Type: application/json\r\n\
         Content-Length: {}\r\n\
         Connection: close\r\n\
         \r\n\
         {}",
        body_str.len(),
        body_str
    );

    stream
        .write_all(request.as_bytes())
        .map_err(|e| format!("发送请求失败: {e}"))?;

    let mut response = String::new();
    stream
        .read_to_string(&mut response)
        .map_err(|e| format!("读取响应失败: {e}"))?;

    // 提取 HTTP body（第一个空行之后的内容）
    let body = response.split("\r\n\r\n").nth(1).unwrap_or("").to_string();

    // 尝试解析 JSON，提取 result 字段用于流式推送
    if let Ok(data) = serde_json::from_str::<serde_json::Value>(&body) {
        if let Some(result) = data.get("result").and_then(|r| r.as_str()) {
            // 按字符分批推送（每 20 个字符发一个 event）
            let chars: Vec<char> = result.chars().collect();
            for chunk in chars.chunks(20) {
                let text: String = chunk.iter().collect();
                let _ = app_handle.emit("task-chunk", text);
                thread::sleep(Duration::from_millis(30)); // 模拟打字速度
            }
        }
        if let Some(error) = data.get("result").and_then(|r| r.as_str()).filter(|r| r.is_empty()) {
            let _ = app_handle.emit("task-chunk", data.get("error").and_then(|e| e.as_str()).unwrap_or("(无输出)"));
        }
    } else {
        // 纯文本直接推送
        for chunk in body.chars().collect::<Vec<_>>().chunks(20) {
            let text: String = chunk.iter().collect();
            let _ = app_handle.emit("task-chunk", text);
            thread::sleep(Duration::from_millis(30));
        }
    }

    // 发送完成信号
    let _ = app_handle.emit("task-done", "");
    Ok(body)
}

#[tauri::command]
fn auto_setup(state: tauri::State<AppState>) -> Result<agent::SetupStatus, String> {
    state.agent.lock().map_err(|e| e.to_string())?.auto_setup()
}

/// 截图：调用系统截图工具，保存到截图目录，返回文件路径
#[tauri::command]
fn take_screenshot(app_handle: tauri::AppHandle) -> Result<String, String> {
    use std::process::Command;

    let ts = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis();
    let filename = format!("screenshot_{}.png", ts);

    // 截图保存路径：桌面/Screenshots/kuafu/
    let pic_dir = dirs::picture_dir().unwrap_or_else(|| PathBuf::from("."));
    let save_dir = pic_dir.join("Screenshots").join("kuafu");
    std::fs::create_dir_all(&save_dir).map_err(|e| format!("创建截图目录失败: {e}"))?;
    let save_path = save_dir.join(&filename);

    #[cfg(target_os = "windows")]
    {
        // Windows: 用 PowerShell 截图（.NET 方式）
        let ps_script = format!(
            r#"
Add-Type -AssemblyName System.Windows.Forms
$screen = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
$bitmap = New-Object System.Drawing.Bitmap $screen.Width, $screen.Height
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
$graphics.CopyFromScreen($screen.Left, $screen.Top, 0, 0, $bitmap.Size)
$bitmap.Save('{}', [System.Drawing.Imaging.ImageFormat]::Png)
$bitmap.Dispose()
$graphics.Dispose()
"#,
            save_path.to_string_lossy().replace("'", "''")
        );
        let output = Command::new("powershell")
            .args(["-NoProfile", "-Command", &ps_script])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::piped())
            .output()
            .map_err(|e| format!("调用 PowerShell 截图失败: {e}"))?;

        if !output.status.success() {
            let err = String::from_utf8_lossy(&output.stderr);
            // 如果 PowerShell 截图失败，回退到 SnippingTool
            if !save_path.exists() {
                Command::new("SnippingTool")
                    .spawn()
                    .map_err(|e| format!("截图失败（PowerShell + SnippingTool 均不可用）: {e}"))?;
                return Err("已启动截图工具，截图后请手动保存到剪贴板".into());
            }
        }
    }

    #[cfg(not(target_os = "windows"))]
    {
        // Linux: 用 import (imagemagick) 或 gnome-screenshot
        let path_arg = save_path.to_string_lossy().to_string();
        let result = Command::new("import")
            .args([&path_arg])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status();
        match result {
            Ok(s) if s.success() => {}
            _ => {
                // 回退 gnome-screenshot
                Command::new("gnome-screenshot")
                    .args(["-f", &path_arg])
                    .stdout(std::process::Stdio::null())
                    .stderr(std::process::Stdio::null())
                    .status()
                    .map_err(|e| format!("截图失败: {e}"))?;
            }
        }
    }

    // 返回文件路径
    let path_str = save_path.to_string_lossy().to_string();
    if save_path.exists() {
        Ok(path_str)
    } else {
        Ok(format!("截图已启动，文件将保存到: {}", path_str))
    }
}

/// 检查更新：读取 GitHub 最新 Release 版本号
#[tauri::command]
fn check_update() -> Result<String, String> {
    use std::io::Read;

    let url = "https://api.github.com/repos/zhugezihou/kuafu/releases/latest";
    let mut resp = ureq::get(url)
        .header("User-Agent", "kuafu-desktop")
        .header("Accept", "application/vnd.github.v3+json")
        .call()
        .map_err(|e| format!("检查更新失败: {e}"))?;

    let mut body = resp.body_mut()
        .read_to_string()
        .map_err(|e| format!("读取响应失败: {e}"))?;

    // 解析 tag_name
    let json: serde_json::Value =
        serde_json::from_str(&body).map_err(|e| format!("解析失败: {e}"))?;
    let tag = json["tag_name"]
        .as_str()
        .unwrap_or("unknown")
        .to_string();
    let html_url = json["html_url"]
        .as_str()
        .unwrap_or("")
        .to_string();

    Ok(serde_json::json!({
        "latest_version": tag,
        "download_url": html_url,
    })
    .to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .setup(|app| {
            let python_dir = match app.path().resource_dir() {
                Ok(dir) => dir.join("python"),
                Err(_) => PathBuf::from("python"),
            };
            let agent_mgr = AgentManager::new(python_dir);
            app.manage(AppState {
                agent: Mutex::new(agent_mgr),
            });

            // 系统托盘：带右键菜单（显示/隐藏/退出）
            use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
            use tauri::menu::{Menu, MenuItem};

            let show_item = MenuItem::with_id(app, "show", "显示窗口", true, None::<&str>)?;
            let hide_item = MenuItem::with_id(app, "hide", "隐藏窗口", true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "退出", true, Some("CmdOrCtrl+Q"))?;
            let menu = Menu::with_items(app, &[&show_item, &hide_item, &quit_item])?;

            let _ = TrayIconBuilder::new()
                .menu(&menu)
                .on_menu_event(|app, event| {
                    let _ = match event.id().as_ref() {
                        "show" => {
                            if let Some(window) = app.get_webview_window("main") {
                                let _ = window.show();
                                let _ = window.set_focus();
                            }
                        }
                        "hide" => {
                            if let Some(window) = app.get_webview_window("main") {
                                let _ = window.hide();
                            }
                        }
                        "quit" => {
                            app.exit(0);
                        }
                        _ => {}
                    };
                })
                .build(app)?;

            // 关闭窗口时最小化到托盘（不退出）
            if let Some(window) = app.get_webview_window("main") {
                let handle = app.handle().clone();
                window.on_window_event(move |event| {
                    if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                        api.prevent_close();
                        let _ = handle.get_webview_window("main").map(|w| w.hide());
                    }
                });
            }

            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            start_agent,
            stop_agent,
            restart_agent,
            agent_status,
            update_agent_config,
            check_setup,
            auto_setup,
            send_task,
            take_screenshot,
            check_update,
            agent::check_local_engines,
            agent::start_llama_server,
            agent::stop_llama_server,
        ])
        .run(tauri::generate_context!())
        .expect("夸父 Desktop 启动失败");
}
