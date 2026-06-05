use serde::{Deserialize, Serialize};
use std::io::Read;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

const GATEWAY_PORT: u16 = 8081;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct AgentConfig {
    pub model_type: String,
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

    /// 找一个能跑夸父的 Python（嵌入式优先，fallback 系统 Python）
    fn find_working_python(&self) -> PathBuf {
        let embedded = self.python_dir.join("python.exe");
        if embedded.exists() {
            let ok = Command::new(&embedded)
                .args(["-c", "import yaml"])
                .stdout(Stdio::null()).stderr(Stdio::null())
                .status().map(|s| s.success()).unwrap_or(false);
            if ok { return embedded; }
        }
        // 系统 Python 可能有 pyyaml
        let system = PathBuf::from("python");
        let ok = Command::new(&system)
            .args(["-c", "import yaml"])
            .stdout(Stdio::null()).stderr(Stdio::null())
            .status().map(|s| s.success()).unwrap_or(false);
        if ok { return system; }
        // 都不可用,返回嵌入式 Python 拿具体错误
        if embedded.exists() { embedded } else { system }
    }

    fn kuafu_dir(&self) -> PathBuf {
        self.python_dir.join("kuafu")
    }

    pub fn start(&self) -> Result<AgentStatus, String> {
        let mut proc = self.process.lock().map_err(|e| e.to_string())?;
        if let Some(ref mut child) = proc.as_mut() {
            if child.try_wait().ok().flatten().is_none() {
                return Ok(AgentStatus {
                    running: true, pid: child.id().into(),
                    gateway_port: GATEWAY_PORT,
                    python_path: self.python_exe().to_string_lossy().to_string(),
                    error: None,
                });
            }
        }

        let python = self.find_working_python();
        let kuafu = self.kuafu_dir();
        let python_str = python.to_string_lossy().to_string();
        let kuafu_str = kuafu.to_string_lossy().to_string();

        if !python.exists() {
            return Err(format!("未找到 Python，请先安装 Python"));
        }
        if !kuafu.join("core").exists() {
            return Err(format!("未找到夸父模块 (尝试路径: {})", kuafu_str));
        }

        let cfg = self.config.lock().map_err(|e| e.to_string())?.clone();

        let mut cmd = Command::new(&python);
        cmd.args(["-m", "core.cli", "gateway", "start", "--port", &GATEWAY_PORT.to_string()])
            .current_dir(&kuafu)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

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

        let mut child = cmd.spawn().map_err(|e| format!("启动夸父失败: {e}"))?;
        let pid = child.id();

        // 等一会儿看进程是否立即退出
        std::thread::sleep(std::time::Duration::from_millis(800));
        if let Some(exit) = child.try_wait().ok().flatten() {
            let mut stderr = String::new();
            if let Some(ref mut pipe) = child.stderr {
                let _ = pipe.read_to_string(&mut stderr);
            }
            let msg = if stderr.is_empty() {
                format!("夸父启动失败 (exit code: {})", exit.code().unwrap_or(-1))
            } else {
                format!("夸父启动失败: {}", stderr.trim())
            };
            if let Ok(mut last) = self.last_error.lock() { *last = msg.clone(); }
            return Err(msg);
        }

        *proc = Some(child);
        if let Ok(mut last) = self.last_error.lock() { last.clear(); }

        Ok(AgentStatus {
            running: true, pid: Some(pid),
            gateway_port: GATEWAY_PORT,
            python_path: python_str,
            error: None,
        })
    }

    pub fn stop(&self) -> Result<(), String> {
        let mut proc = self.process.lock().map_err(|e| e.to_string())?;
        if let Some(mut child) = proc.take() {
            child.kill().map_err(|e| format!("停止夸父失败: {e}"))?;
            child.wait().ok();
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
            running, pid: proc.as_ref().map(|p| p.id()),
            gateway_port: GATEWAY_PORT,
            python_path: self.find_working_python().to_string_lossy().to_string(),
            error: err,
        }
    }

    pub fn restart(&self) -> Result<AgentStatus, String> {
        self.stop()?;
        self.start()
    }

    fn python_exe(&self) -> PathBuf {
        self.find_working_python()
    }

    fn get_last_error(&self) -> String {
        self.last_error.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }
}
