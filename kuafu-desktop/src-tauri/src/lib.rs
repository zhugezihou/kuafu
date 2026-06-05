use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_notification::init())
        .setup(|app| {
            // 系统托盘
            let _ = app.tray().map(|t| t.get_item("main"));
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("夸父 Desktop 启动失败");
}
