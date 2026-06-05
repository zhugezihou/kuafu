mod agent;

use agent::AgentManager;
use serde_json::json;
use std::path::PathBuf;
use std::sync::Mutex;
use tauri::{Emitter, Manager};

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
async fn send_task(task: String) -> Result<String, String> {
    let client = reqwest::Client::new();
    let resp = client
        .post(format!("http://localhost:{}/api/task", 8081))
        .json(&json!({"task": task, "mode": "standard", "sync": true}))
        .send()
        .await
        .map_err(|e| format!("请求失败: {}", e))?;
    let data: serde_json::Value = resp.json().await.map_err(|e| e.to_string())?;
    Ok(data["result"].as_str().unwrap_or("(无输出)").to_string())
}

#[tauri::command]
async fn send_task_stream(task: String, app: tauri::AppHandle) -> Result<String, String> {
    let client = reqwest::Client::new();
    let resp = client
        .post(format!("http://localhost:{}/api/task", 8081))
        .json(&json!({"task": task, "mode": "standard", "sync": false}))
        .send()
        .await
        .map_err(|e| format!("请求失败: {}", e))?;

    use futures_util::StreamExt;
    let mut stream = resp.bytes_stream();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|e| e.to_string())?;
        let text = String::from_utf8_lossy(&chunk).to_string();
        for line in text.lines() {
            if let Some(data) = line.strip_prefix("data: ") {
                app.emit("stream-chunk", data).map_err(|e| e.to_string())?;
            }
        }
    }
    app.emit("stream-done", ()).map_err(|e| e.to_string())?;
    Ok("ok".to_string())
}

#[tauri::command]
async fn get_status(app: tauri::AppHandle) -> Result<serde_json::Value, String> {
    let client = reqwest::Client::new();
    let resp = client
        .get(format!("http://localhost:{}/api/status", 8081))
        .send()
        .await
        .map_err(|_| json!({"status": "offline", "version": "?", "model": "?", "backend": "?", "evolution": {"total": 0}}))?;
    resp.json().await.map_err(|e| e.to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .setup(|app| {
            // 获取资源目录（打包的 Python + 夸父源码所在目录）
            let resource_dir = app
                .path()
                .resource_dir()
                .unwrap_or_else(|_| PathBuf::from("."));
            let python_dir = resource_dir.join("python");
            app.manage(AppState {
                agent: Mutex::new(AgentManager::new(python_dir)),
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            start_agent,
            stop_agent,
            agent_status,
            send_task,
            send_task_stream,
            get_status,
        ])
        .run(tauri::generate_context!())
        .expect("夸父 Desktop 启动失败");
}
