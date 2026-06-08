mod agent;

use agent::AgentManager;
use std::path::PathBuf;
use std::sync::Mutex;
use tauri::Manager;

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
        ])
        .run(tauri::generate_context!())
        .expect("夸父 Desktop 启动失败");
}
