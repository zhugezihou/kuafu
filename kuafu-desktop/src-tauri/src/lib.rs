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
fn restart_agent(state: tauri::State<AppState>) -> Result<agent::AgentStatus, String> {
    state.agent.lock().map_err(|e| e.to_string())?.restart()
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
async fn send_task(task: String) -> Result<String, String> {
    let client = reqwest::Client::new();
    let resp = client
        .post(format!("http://localhost:{}/api/task", 8081))
        .json(&json!({"task": task, "mode": "standard", "sync": true}))
        .send()
        .await
        .map_err(|e| format!("请求失败: {e}"))?;
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
        .map_err(|e| format!("请求失败: {e}"))?;

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
async fn get_status() -> Result<serde_json::Value, String> {
    let client = reqwest::Client::new();
    match client
        .get(format!("http://localhost:{}/api/status", 8081))
        .send()
        .await
    {
        Ok(resp) => resp.json().await.map_err(|e| e.to_string()),
        Err(_) => Ok(json!({"status": "offline"})),
    }
}

#[tauri::command]
async fn get_sessions_from_gateway() -> Result<serde_json::Value, String> {
    let client = reqwest::Client::new();
    match client
        .get(format!("http://localhost:{}/api/sessions", 8081))
        .send()
        .await
    {
        Ok(resp) => resp.json().await.map_err(|e| e.to_string()),
        Err(_) => Ok(json!({"sessions": []})),
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .setup(|app| {
            let resource_dir = app
                .path()
                .resource_dir()
                .unwrap_or_else(|_| PathBuf::from("."));
            let python_dir = resource_dir.join("python");
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
            send_task,
            send_task_stream,
            get_status,
            get_sessions_from_gateway,
        ])
        .run(tauri::generate_context!())
        .expect("夸父 Desktop 启动失败");
}
