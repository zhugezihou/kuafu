use serde::{Deserialize, Serialize};
use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

const GATEWAY_PORT: u16 = 8081;

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
}

impl AgentManager {
    pub fn new(python_dir: PathBuf) -> Self {
        Self {
            process: Mutex::new(None),
            python_dir,
        }
    }

    fn python_exe(&self) -> PathBuf {
        // 嵌入式 Python 路径
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

    /// 启动夸父引擎子进程
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

        // 检查 python 是否存在
        if !python.exists() {
            return Err(format!(
                "未找到 Python (尝试路径: {})。请先安装 Python 或运行 pip install kuafu",
                python_str
            ));
        }

        if !kuafu.join("core").exists() {
            return Err(format!(
                "未找到夸父模块 (尝试路径: {})。安装包可能不完整",
                kuafu_str
            ));
        }

        let child = Command::new(&python)
            .args(["-m", "core.main", "--gateway-port", &GATEWAY_PORT.to_string(), "--gateway-only"])
            .current_dir(&kuafu)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .map_err(|e| format!("启动夸父失败: {}", e))?;

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
            child.kill().map_err(|e| format!("停止夸父失败: {}", e))?;
            child.wait().ok();
        }
        Ok(())
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
