mod agent;

use agent::AgentManager;
use std::path::PathBuf;
use std::sync::Mutex;
use tauri::Manager;

struct AppState {
    agent: Mutex<AgentManager>,
}

#[tauri::command]
fn ping() -> String {
    "pong".into()
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
        .invoke_handler(tauri::generate_handler![ping])
        .run(tauri::generate_context!())
        .expect("夸父 Desktop 启动失败");
}
