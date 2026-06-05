use serde::{Deserialize, Serialize};
use std::io::{BufRead, BufReader, Write};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;

const GATEWAY_PORT: u16 = 8081;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct AgentStatus {
    pub running: bool,
    pub pid: Option<u32>,
    pub gateway_port: u16,
}

pub struct AgentManager {
    process: Mutex<Option<Child>>,
}

impl AgentManager {
    pub fn new() -> Self {
        Self {
            process: Mutex::new(None),
        }
    }

    /// 启动夸父引擎子进程
    pub fn start(&self) -> Result<AgentStatus, String> {
        let mut proc = self.process.lock().map_err(|e| e.to_string())?;
        if proc.is_some() {
            return Ok(AgentStatus {
                running: true,
                pid: proc.as_ref().and_then(|p| p.id().into()),
                gateway_port: GATEWAY_PORT,
            });
        }

        let child = Command::new("python")
            .args([
                "-m",
                "core.main",
                "--gateway-port",
                &GATEWAY_PORT.to_string(),
                "--gateway-only",
            ])
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
        }
    }
}
