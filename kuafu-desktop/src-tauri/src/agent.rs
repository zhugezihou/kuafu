use serde::{Deserialize, Serialize};
use std::io::Read;
use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

const GATEWAY_PORT: u16 = 8081;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct AgentConfig {
    pub model_type: String,
    pub local_model_path: String,
    pub local_llm_endpoint: String,
    #[serde(default = "default_local_context_length")]
    pub local_context_length: u32,
    #[serde(default = "default_local_gpu_layers")]
    pub local_gpu_layers: u32,
    pub cloud_api_key: String,
    #[serde(default)]
    pub cloud_base_url: String,
    #[serde(default = "default_cloud_provider")]
    pub cloud_provider: String,
    pub cloud_model: String,
}

fn default_cloud_provider() -> String {
    "deepseek".to_string()
}

fn default_local_context_length() -> u32 { 32768 }

fn default_local_gpu_layers() -> u32 { 999 }

impl Default for AgentConfig {
    fn default() -> Self {
        Self {
            model_type: "local".into(),
            local_model_path: String::new(),
            local_llm_endpoint: "http://localhost:8080".into(),
            local_context_length: 32768,
            local_gpu_layers: 999,
            cloud_api_key: String::new(),
            cloud_base_url: "https://api.deepseek.com".into(),
            cloud_provider: "deepseek".into(),
            cloud_model: "deepseek-chat".into(),
        }
    }
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct AgentStatus {
    pub running: bool,
    pub pid: Option<u32>,
    pub gateway_port: u16,
    pub python_path: String,
    pub error: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct SetupStatus {
    pub python_found: bool,
    pub pyyaml_installed: bool,
    pub kuafu_found: bool,
    pub gateway_running: bool,
    pub python_path: String,
    pub error: Option<String>,
    pub setup_complete: bool,
}

pub struct AgentManager {
    process: Mutex<Option<Child>>,
    python_dir: PathBuf,
    config: Mutex<AgentConfig>,
    last_error: Mutex<String>,
}

impl AgentManager {
    pub fn new(python_dir: PathBuf) -> Self {
        Self {
            process: Mutex::new(None),
            python_dir,
            config: Mutex::new(AgentConfig::default()),
            last_error: Mutex::new(String::new()),
        }
    }

    pub fn update_config(&self, config: AgentConfig) {
        if let Ok(mut c) = self.config.lock() {
            *c = config;
        }
    }

    fn embedded_python(&self) -> PathBuf {
        self.python_dir.join("python.exe")
    }

    fn kuafu_dir(&self) -> PathBuf {
        self.python_dir.join("kuafu")
    }

    /// Windows: py.exe (Python Launcher) 优先，再 python3, 再 python
    fn find_system_python() -> Option<PathBuf> {
        for name in &["py", "python3", "python"] {
            let p = PathBuf::from(name);
            // 先查 PATH 版本号
            let ok = Command::new(&p)
                .args(["--version"])
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status()
                .map(|s| s.success())
                .unwrap_or(false);
            if ok {
                // py 是 launcher，换成真实路径
                if name == &"py" {
                    // py -c "import sys; print(sys.executable)" 出完整路径
                    if let Ok(out) = Command::new("py")
                        .args(["-c", "import sys; print(sys.executable)"])
                        .stdout(Stdio::piped())
                        .stderr(Stdio::null())
                        .output()
                    {
                        let path = String::from_utf8_lossy(&out.stdout).trim().to_string();
                        if !path.is_empty() {
                            return Some(PathBuf::from(path));
                        }
                    }
                }
                return Some(p);
            }
        }
        None
    }

    /// 检查环境状态
    pub fn check_setup(&self) -> SetupStatus {
        let embedded = self.embedded_python();
        let embedded_exists = embedded.exists();
        let system_py = Self::find_system_python();

        // 优先用系统 Python（避免嵌入式 Python 的 python._pth 路径问题）
        let (python_path, python_found) = if let Some(ref p) = system_py {
            let pyyaml_ok = Command::new(p)
                .args(["-c", "import yaml"])
                .stdout(Stdio::null()).stderr(Stdio::null())
                .status().map(|s| s.success()).unwrap_or(false);
            if pyyaml_ok {
                (p.to_string_lossy().to_string(), true)
            } else if embedded_exists {
                (embedded.to_string_lossy().to_string(), true)
            } else {
                (p.to_string_lossy().to_string(), true)
            }
        } else if embedded_exists {
            (embedded.to_string_lossy().to_string(), true)
        } else {
            (String::new(), false)
        };

        if !python_found {
            return SetupStatus {
                python_found: false,
                pyyaml_installed: false,
                kuafu_found: false,
                gateway_running: false,
                python_path: String::new(),
                error: Some("未找到 Python 环境，请先安装 Python 3.11+".into()),
                setup_complete: false,
            };
        }

        let py = PathBuf::from(&python_path);

        let pyyaml_ok = Command::new(&py)
            .args(["-c", "import yaml"])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map(|s| s.success())
            .unwrap_or(false);

        let kuafu = self.kuafu_dir();
        let kuafu_ok = kuafu.join("core").exists();

        let gateway_ok = TcpStream::connect_timeout(
            &"127.0.0.1:8081".parse().unwrap(),
            Duration::from_millis(500),
        )
        .is_ok();

        let error = if !kuafu_ok {
            Some("未找到夸父模块".into())
        } else if !pyyaml_ok {
            Some("缺少 PyYAML 依赖".into())
        } else {
            None
        };

        SetupStatus {
            python_found: true,
            pyyaml_installed: pyyaml_ok,
            kuafu_found: kuafu_ok,
            gateway_running: gateway_ok,
            python_path,
            error,
            setup_complete: kuafu_ok && pyyaml_ok,
        }
    }

    /// 自动修复环境
    pub fn auto_setup(&self) -> Result<SetupStatus, String> {
        let mut status = self.check_setup();

        if !status.python_found {
            return Err("未找到 Python，请手动安装 https://www.python.org/downloads/".into());
        }

        let py = PathBuf::from(&status.python_path);

        if !status.pyyaml_installed {
            // 先试试系统 Python 的 pip（可能比嵌入式 Python 更可靠）
            let sys_py = Self::find_system_python();
            let pip_py = sys_py.as_ref().unwrap_or(&py);

            let pip_result = Command::new(pip_py)
                .args(["-m", "pip", "install", "pyyaml", "--quiet"])
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
            if let Ok(code) = pip_result {
                if code.success() {
                    status.pyyaml_installed = true;
                }
            }

            if !status.pyyaml_installed {
                let _ = Command::new(pip_py)
                    .args(["-m", "ensurepip", "--upgrade", "--quiet"])
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status();
                let _ = Command::new(pip_py)
                    .args(["-m", "pip", "install", "pyyaml", "--quiet"])
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status();
                let check = Command::new(pip_py)
                    .args(["-c", "import yaml"])
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status()
                    .map(|s| s.success())
                    .unwrap_or(false);
                status.pyyaml_installed = check;
            }

            // 如果系统 Python 装上了 pyyaml，就用系统 Python 启动
            if status.pyyaml_installed {
                if let Some(ref sys) = sys_py {
                    status.python_path = sys.to_string_lossy().to_string();
                }
            }
        }

        if !status.kuafu_found {
            status.error = Some("夸父模块缺失，安装包可能不完整".into());
            return Ok(status);
        }

        status.setup_complete = status.pyyaml_installed && status.kuafu_found;
        Ok(status)
    }

    /// 启动夸父 Gateway
    pub fn start(&self) -> Result<AgentStatus, String> {
        let mut proc = self.process.lock().map_err(|e| e.to_string())?;
        if let Some(ref mut child) = proc.as_mut() {
            if child.try_wait().ok().flatten().is_none() {
                return Ok(AgentStatus {
                    running: true,
                    pid: child.id().into(),
                    gateway_port: GATEWAY_PORT,
                    python_path: self.find_python().to_string_lossy().to_string(),
                    error: None,
                });
            }
        }

        let setup = self.auto_setup()?;
        if !setup.setup_complete {
            let err = setup.error.unwrap_or_else(|| "环境准备未完成".into());
            return Err(err);
        }

        // 独立确定 Python 路径：先找嵌入式，再找系统 Python
        let python = self.find_python();
        let kuafu = self.kuafu_dir();
        let kuafu_str = kuafu.to_string_lossy().to_string();
        let python_str = python.to_string_lossy().to_string();

        // 验证 python 可执行文件存在
        if !python.exists() && !python.is_absolute() {
            // 可能是 PATH 中的名称（如 "python"），尝试 which/where
            // 在 Windows 上直接用
        }
        if !python.exists() && python.is_absolute() {
            return Err(format!("Python 可执行文件不存在: {}", python_str));
        }

        if !kuafu.join("core").exists() {
            return Err(format!("未找到夸父模块 (路径: {})", kuafu_str));
        }

        let cfg = self.config.lock().map_err(|e| e.to_string())?.clone();

        let mut cmd = Command::new(&python);
        // 使用 -c 脚本方式启动（避免 python._pth 禁用 PYTHONPATH 的问题）
        let bootstrap = format!(
            "import sys; sys.path.insert(0, r'{}'); sys.argv = ['core.cli', 'gateway', 'start', '--port', '{}']; from core.cli import main; sys.exit(main())",
            kuafu_str, GATEWAY_PORT
        );

        // Debug: 打印启动命令
        eprintln!("[Hermes] starting: {} -c ...", python_str);
        eprintln!("[Hermes] sys.path: {}", kuafu_str);
        eprintln!("[Hermes] KUAFFU_PROVIDERS={:?}, KUAFFU_DESKTOP=1, KUAFFU_LLM_BACKEND={:?}",
            cfg.model_type,
            if cfg.model_type == "cloud" { &cfg.cloud_provider } else { "llama" }
        );

        cmd.args(["-c", &bootstrap])
        .stdout(Stdio::null())
        .stderr(Stdio::piped());

        // Desktop 模式下，stderr 重定向日志文件，方便排查
        let log_dir = self.python_dir.join("logs");
        let _ = std::fs::create_dir_all(&log_dir);
        let ts = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs();
        let log_file = log_dir.join(format!("gateway_{}.log", ts));
        if let Ok(file) = std::fs::File::create(&log_file) {
            cmd.stderr(file);
        }

        cmd.env("KUAFFU_GATEWAY_PORT", GATEWAY_PORT.to_string());
        cmd.env("KUAFFU_DESKTOP", "1");  // Desktop 模式：禁用微信/飞书等交互通道
        if cfg.model_type == "cloud" {
            cmd.env("KUAFFU_LLM_BACKEND", "cloud");
            // 根据 provider 设置环境变量
            match cfg.cloud_provider.as_str() {
                "openai" => {
                    cmd.env("KUAFFU_PROVIDERS", "openai");
                    cmd.env("OPENAI_API_KEY", cfg.cloud_api_key.clone());
                    cmd.env("OPENAI_BASE_URL", cfg.cloud_base_url.clone());
                    cmd.env("OPENAI_MODEL", cfg.cloud_model.clone());
                }
                "custom" => {
                    cmd.env("KUAFFU_PROVIDERS", "custom");
                    cmd.env("CUSTOM_API_KEY", cfg.cloud_api_key.clone());
                    cmd.env("CUSTOM_BASE_URL", cfg.cloud_base_url.clone());
                    cmd.env("CUSTOM_MODEL", cfg.cloud_model.clone());
                }
                _ => { // deepseek (default)
                    cmd.env("KUAFFU_PROVIDERS", "deepseek");
                    cmd.env("DEEPSEEK_API_KEY", cfg.cloud_api_key.clone());
                    cmd.env("DEEPSEEK_BASE_URL", cfg.cloud_base_url.clone());
                    cmd.env("DEEPSEEK_MODEL", cfg.cloud_model.clone());
                }
            }
        } else {
            cmd.env("KUAFFU_LLM_BACKEND", "llama");
            cmd.env("KUAFFU_LLM_ENDPOINT", cfg.local_llm_endpoint.clone());
            if !cfg.local_model_path.is_empty() {
                cmd.env("KUAFFU_LLM_MODEL_PATH", cfg.local_model_path.clone());
            }
        }

        let mut child = cmd.spawn().map_err(|e| format!("启动夸父失败: {e}"))?;
        let pid = child.id();

        // 轮询 5 秒: 每 500ms 检查进程退出 + Gateway HTTP 就绪
        let mut gateway_ready = false;
        for _ in 0..10 {
            std::thread::sleep(Duration::from_millis(500));

            // 进程是否已退出？
            if let Some(exit) = child.try_wait().ok().flatten() {
                // 确保读完 stderr（等一小会儿让 pipe 缓冲刷出）
                std::thread::sleep(Duration::from_millis(100));
                let mut stderr = String::new();
                if let Some(ref mut pipe) = child.stderr {
                    let _ = pipe.read_to_string(&mut stderr);
                }
                // 如果 stderr 为空，也可能 stdout 里有错误
                let mut stdout = String::new();
                if stderr.is_empty() {
                    if let Some(ref mut pipe) = child.stdout {
                        let _ = pipe.read_to_string(&mut stdout);
                    }
                }
                let output = if !stderr.is_empty() {
                    stderr.trim().to_string()
                } else if !stdout.is_empty() {
                    stdout.trim().to_string()
                } else {
                    String::new()
                };
                let msg = if output.is_empty() {
                    format!("夸父启动失败 (exit code: {})", exit.code().unwrap_or(-1))
                } else {
                    // 截断过长输出（最多 300 字符）
                    let truncated = if output.len() > 300 {
                        format!("{}...", &output[..300])
                    } else {
                        output
                    };
                    format!("夸父启动失败: {}", truncated)
                };
                if let Ok(mut last) = self.last_error.lock() {
                    *last = msg.clone();
                }
                return Err(msg);
            }

            // Gateway HTTP 端口起来了吗？
            if TcpStream::connect_timeout(
                &"127.0.0.1:8081".parse().unwrap(),
                Duration::from_millis(200),
            )
            .is_ok()
            {
                gateway_ready = true;
                break;
            }
        }

        *proc = Some(child);
        if let Ok(mut last) = self.last_error.lock() {
            last.clear();
        }

        Ok(AgentStatus {
            running: true,
            pid: Some(pid),
            gateway_port: GATEWAY_PORT,
            python_path: python_str,
            error: if gateway_ready {
                None
            } else {
                Some("网关启动较慢，健康检查将继续等待".into())
            },
        })
    }

    /// Graceful stop: SIGTERM → 等 2s → SIGKILL
    pub fn stop(&self) -> Result<(), String> {
        let mut proc = self.process.lock().map_err(|e| e.to_string())?;
        if let Some(mut child) = proc.take() {
            // Windows 上 kill 相当于 TerminateProcess
            #[cfg(windows)]
            {
                // Windows: 先 taskkill（不加 /F = 发 WM_CLOSE，相当于 SIGTERM）
                let pid = child.id();
                let _ = std::process::Command::new("taskkill")
                    .args(["/PID", &pid.to_string()])
                    .stdout(std::process::Stdio::null())
                    .stderr(std::process::Stdio::null())
                    .status();
                // 等 2 秒让进程处理收尾
                for _ in 0..4 {
                    if child.try_wait().ok().flatten().is_some() {
                        break;
                    }
                    std::thread::sleep(Duration::from_millis(500));
                }
                let _ = child.wait();
            }
            #[cfg(not(windows))]
            {
                // 跨平台 SIGTERM: 用 libc 或 std::process::Command
                #[cfg(target_family = "unix")]
                {
                    
                    // 用 kill 命令更可靠
                    let _ = std::process::Command::new("kill")
                        .args(["-TERM", &child.id().to_string()])
                        .stdout(std::process::Stdio::null())
                        .stderr(std::process::Stdio::null())
                        .status();
                }
                #[cfg(not(target_family = "unix"))]
                {
                    let _ = child.kill();
                }
                std::thread::sleep(Duration::from_secs(2));
                let _ = child.wait();
            }
        }
        Ok(())
    }

    pub fn status(&self) -> AgentStatus {
        let mut proc = self.process.lock().unwrap();
        let (running, err) = if let Some(ref mut child) = *proc {
            match child.try_wait() {
                Ok(Some(_)) => (false, Some(self.get_last_error())),
                Ok(None) => (true, None),
                Err(e) => (false, Some(format!("进程检查失败: {e}"))),
            }
        } else {
            (false, Some(self.get_last_error()))
        };
        AgentStatus {
            running,
            pid: proc.as_ref().map(|p| p.id()),
            gateway_port: GATEWAY_PORT,
            python_path: self.find_python().to_string_lossy().to_string(),
            error: err,
        }
    }

    pub fn restart(&self) -> Result<AgentStatus, String> {
        self.stop()?;
        self.start()
    }

    fn find_python(&self) -> PathBuf {
        // Desktop 模式优先用系统 Python（已有 pyyaml），备用嵌入式
        if let Some(sys) = Self::find_system_python() {
            let ok = Command::new(&sys)
                .args(["-c", "import yaml"])
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status()
                .map(|s| s.success())
                .unwrap_or(false);
            if ok {
                return sys;
            }
        }
        // 兜底：嵌入式 Python
        self.embedded_python()
    }

    fn get_last_error(&self) -> String {
        self.last_error
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .clone()
    }
}

// ── P3: 本地模型管理 ──

/// 检测本地可用的推理引擎
#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct LocalEngineStatus {
    pub llama_server: bool,
    pub ollama: bool,
    pub llama_server_running: bool,
    pub ollama_running: bool,
    pub models_dir: String,
    #[serde(default)]
    pub models: Vec<ModelInfo>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct ModelInfo {
    pub name: String,
    pub path: String,
    pub size_mb: f64,
    pub quant: Option<String>,
}

/// 检测本地推理引擎状态
#[tauri::command]
pub fn check_local_engines(python_dir: String) -> Result<LocalEngineStatus, String> {
    use std::process::Command;
    let models_dir = PathBuf::from(&python_dir).join("models");

    // 检测 llama-server
    let llama_server = Command::new("llama-server")
        .arg("--version")
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false);

    // 检测 ollama
    let ollama = Command::new("ollama")
        .arg("--version")
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false);

    // 检测是否正在运行
    let llama_server_running = std::net::TcpStream::connect_timeout(
        &"127.0.0.1:8080".parse().unwrap(),
        std::time::Duration::from_millis(500),
    )
    .is_ok();

    let ollama_running = std::net::TcpStream::connect_timeout(
        &"127.0.0.1:11434".parse().unwrap(),
        std::time::Duration::from_millis(500),
    )
    .is_ok();

    // 扫描 models 目录
    let mut models = Vec::new();
    if models_dir.exists() {
        if let Ok(entries) = std::fs::read_dir(&models_dir) {
            for entry in entries.flatten() {
                let path = entry.path();
                if path.extension().map_or(false, |e| e == "gguf") {
                    let size = std::fs::metadata(&path).map(|m| m.len() as f64 / 1_048_576.0).unwrap_or(0.0);
                    let name = path.file_stem().unwrap_or_default().to_string_lossy().to_string();
                    let quant = name.rsplit('-').next().map(|s| s.to_string());
                    models.push(ModelInfo {
                        name: path.file_name().unwrap_or_default().to_string_lossy().to_string(),
                        path: path.to_string_lossy().to_string(),
                        size_mb: (size * 10.0).round() / 10.0,
                        quant,
                    });
                }
            }
        }
    }

    // 按大小降序排列
    models.sort_by(|a, b| b.size_mb.partial_cmp(&a.size_mb).unwrap_or(std::cmp::Ordering::Equal));

    Ok(LocalEngineStatus {
        llama_server,
        ollama,
        llama_server_running,
        ollama_running,
        models_dir: models_dir.to_string_lossy().to_string(),
        models,
    })
}

/// 启动本地 llama-server
#[tauri::command]
pub fn start_llama_server(
    python_dir: String,
    model_path: String,
    context_length: u32,
    gpu_layers: u32,
) -> Result<String, String> {
    use std::process::Command;

    // 验证模型文件存在
    let model = PathBuf::from(&model_path);
    if !model.exists() {
        return Err(format!("模型文件不存在: {}", model_path));
    }

    let log_dir = PathBuf::from(&python_dir).join("logs");
    let _ = std::fs::create_dir_all(&log_dir);
    let log_file = log_dir.join("llama-server.log");

    let mut cmd = Command::new("llama-server");
    cmd.args([
        "-m", &model_path,
        "--host", "127.0.0.1",
        "--port", "8080",
        "-c", &context_length.to_string(),
        "-ngl", &gpu_layers.to_string(),
    ])
    .stdout(std::process::Stdio::null())
    .stderr(std::process::Stdio::null());

    // stderr 重定向到日志文件
    if let Ok(file) = std::fs::File::create(&log_file) {
        cmd.stderr(file);
    }

    cmd.spawn()
        .map_err(|e| format!("启动 llama-server 失败: {e}"))?;

    // 等待 3 秒检测是否正常启动
    std::thread::sleep(std::time::Duration::from_secs(3));
    let running = std::net::TcpStream::connect_timeout(
        &"127.0.0.1:8080".parse().unwrap(),
        std::time::Duration::from_millis(500),
    )
    .is_ok();

    if running {
        Ok("llama-server 已启动".into())
    } else {
        Err("llama-server 进程已启动但端口 8080 未就绪，请查看日志文件".into())
    }
}

/// 停止 llama-server（通过 kill 命令）
#[tauri::command]
pub fn stop_llama_server() -> Result<String, String> {
    #[cfg(target_os = "windows")]
    {
        let _ = std::process::Command::new("taskkill")
            .args(["/IM", "llama-server.exe", "/F"])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status();
    }
    #[cfg(not(target_os = "windows"))]
    {
        let _ = std::process::Command::new("pkill")
            .args(["-f", "llama-server"])
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status();
    }
    std::thread::sleep(std::time::Duration::from_millis(500));
    Ok("llama-server 已停止".into())
}
