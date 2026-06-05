use serde::{Deserialize, Serialize};
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

const GATEWAY_PORT: u16 = 8081;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct AgentConfig {
    pub model_type: String,        // "local" | "cloud"
    pub local_model_path: String,
    pub local_llm_endpoint: String,
    pub cloud_api_key: String,
    pub cloud_model: String,
}

impl Default for AgentConfig {
    fn default() -> Self {
        Self {
            model_type: "local".into(),
            local_model_path: String::new(),
            local_llm_endpoint: "http://localhost:8080".into(),
            cloud_api_key: String::new(),
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

pub struct AgentManager {
    process: Mutex<Option<Child>>,
    python_dir: PathBuf,
    config: Mutex<AgentConfig>,
}

impl AgentManager {
    pub fn new(python_dir: PathBuf) -> Self {
        Self {
            process: Mutex::new(None),
            python_dir,
            config: Mutex::new(AgentConfig::default()),
        }
    }

    pub fn update_config(&self, config: AgentConfig) {
        if let Ok(mut c) = self.config.lock() {
            *c = config;
        }
    }

    fn python_exe(&self) -> PathBuf {
        let p = self.python_dir.join("python.exe");
        if p.exists() {
            return p;
        }
        // 回退到系统 python
        PathBuf::from("python")
    }

    fn kuafu_dir(&self) -> PathBuf {
        self.python_dir.join("kuafu")
    }

    /// 启动夸父 Gateway 子进程
    pub fn start(&self) -> Result<AgentStatus, String> {
        let mut proc = self.process.lock().map_err(|e| e.to_string())?;
        if proc.is_some() {
            let running = proc
                .as_mut()
                .map(|p| p.try_wait().ok().flatten().is_none())
                .unwrap_or(false);
            if running {
                return Ok(AgentStatus {
                    running: true,
                    pid: proc.as_ref().and_then(|p| p.id().into()),
                    gateway_port: GATEWAY_PORT,
                    python_path: self.python_exe().to_string_lossy().to_string(),
                    error: None,
                });
            }
        }

        let python = self.python_exe();
        let kuafu = self.kuafu_dir();
        let kuafu_str = kuafu.to_string_lossy().to_string();
        let python_str = python.to_string_lossy().to_string();

        if !python.exists() {
            return Err(format!(
                "未找到 Python (尝试路径: {})。请先安装 Python",
                python_str
            ));
        }

        if !kuafu.join("core").exists() {
            return Err(format!(
                "未找到夸父模块 (尝试路径: {})。安装包可能不完整",
                kuafu_str
            ));
        }

        // 读取配置
        let cfg = self.config.lock().map_err(|e| e.to_string())?.clone();

        // 构建环境变量
        let mut cmd = Command::new(&python);
        cmd.args([
            "-m", "core.cli", "gateway", "start",
            "--port", &GATEWAY_PORT.to_string(),
        ])
            .current_dir(&kuafu)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        // 通过环境变量传递配置
        cmd.env("KUAFFU_GATEWAY_PORT", GATEWAY_PORT.to_string());
        if cfg.model_type == "cloud" {
            cmd.env("KUAFFU_LLM_BACKEND", "openai");
            cmd.env("OPENAI_API_KEY", cfg.cloud_api_key.clone());
            cmd.env("KUAFFU_LLM_MODEL", cfg.cloud_model.clone());
        } else {
            cmd.env("KUAFFU_LLM_BACKEND", "llama");
            cmd.env("KUAFFU_LLM_ENDPOINT", cfg.local_llm_endpoint.clone());
            if !cfg.local_model_path.is_empty() {
                cmd.env("KUAFFU_LLM_MODEL_PATH", cfg.local_model_path.clone());
            }
        }

        let child = cmd.spawn()
            .map_err(|e| format!("启动夸父失败: {e}"))?;

        let pid = child.id();
        *proc = Some(child);

        Ok(AgentStatus {
            running: true,
            pid: Some(pid),
            gateway_port: GATEWAY_PORT,
            python_path: python_str,
            error: None,
        })
    }

    /// 停止夸父子进程
    pub fn stop(&self) -> Result<(), String> {
        let mut proc = self.process.lock().map_err(|e| e.to_string())?;
        if let Some(mut child) = proc.take() {
            child.kill().map_err(|e| format!("停止夸父失败: {e}"))?;
            child.wait().ok();
        }
        Ok(())
    }

    /// 重启夸父子进程
    pub fn restart(&self) -> Result<AgentStatus, String> {
        self.stop()?;
        self.start()
    }

    /// 获取状态
    pub fn status(&self) -> AgentStatus {
        let mut proc = self.process.lock().unwrap();
        let running = if let Some(ref mut child) = *proc {
            child.try_wait().ok().flatten().is_none()
        } else {
            false
        };
        AgentStatus {
            running,
            pid: proc.as_ref().map(|p| p.id()),
            gateway_port: GATEWAY_PORT,
            python_path: self.python_exe().to_string_lossy().to_string(),
            error: None,
        }
    }
}
