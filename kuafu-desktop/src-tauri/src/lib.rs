mod agent;

use agent::AgentManager;
use tauri::Manager;
use std::sync::Mutex;

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
    state.agent.lock().map(|mut a| a.status()).unwrap_or(agent::AgentStatus {
        running: false,
        pid: None,
        gateway_port: 8081,
    })
}

#[tauri::command]
async fn send_task(task: String) -> Result<String, String> {
    let client = reqwest::Client::new();
    let resp = client
        .post(format!("http://localhost:{}/api/task", 8081))
        .json(&serde_json::json!({"task": task, "mode": "standard", "sync": true}))
        .send()
        .await
        .map_err(|e| format!("请求失败: {}", e))?;
    let data: serde_json::Value = resp.json().await.map_err(|e| e.to_string())?;
    Ok(data["result"].as_str().unwrap_or("(无输出)").to_string())
}

#[tauri::command]
async fn get_status() -> Result<serde_json::Value, String> {
    let client = reqwest::Client::new();
    let resp = client
        .get(format!("http://localhost:{}/api/status", 8081))
        .send()
        .await
        .map_err(|e| format!("请求失败: {}", e))?;
    resp.json().await.map_err(|e| e.to_string())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .manage(AppState {
            agent: Mutex::new(AgentManager::new()),
        })
        .invoke_handler(tauri::generate_handler![
            start_agent,
            stop_agent,
            agent_status,
            send_task,
            get_status,
        ])
        .run(tauri::generate_context!())
        .expect("夸父 Desktop 启动失败");
}
