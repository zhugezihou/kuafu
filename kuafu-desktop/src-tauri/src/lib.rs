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
        ])
        .run(tauri::generate_context!())
        .expect("夸父 Desktop 启动失败");
}
