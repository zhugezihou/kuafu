/// <reference types="svelte" />

import { log } from "./debug";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

// Gateway API 客户端 — 前端直连 localhost:8081
const GATEWAY_URL = "http://localhost:8081";

export interface Message {
  role: "user" | "assistant" | "tool" | "system";
  content: string;
  id?: string;
  timestamp?: number;
  edited?: boolean;
}

export interface Session {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  message_count: number;
}

/** 通过 Tauri invoke 发送消息（绕过 WebView CORS 限制） */
export async function sendMessage(
  task: string,
  mode = "standard"
): Promise<string> {
  log("debug", `sendMessage: task="${task.slice(0, 50)}..." mode=${mode}`);
  try {
    const data = await invoke("send_task", { task }) as string;
    log("info", `sendMessage: result_len=${(data || "").length}`);
    return data || "(无输出)";
  } catch (e: any) {
    log("error", `sendMessage failed: ${e.message || e}`);
    return `错误: ${e.message || e}`;
  }
}

/** 带重试的健康检查：每秒一次，最多 retries 次 */
export async function waitForGateway(
  retries = 15,
  interval = 1000
): Promise<boolean> {
  for (let i = 0; i < retries; i++) {
    try {
      const resp = await fetch(`${GATEWAY_URL}/api/status`, {
        signal: AbortSignal.timeout(3000),
      });
      if (resp.ok) return true;
    } catch {
      // 还没就绪
    }
    if (i < retries - 1) {
      await new Promise((r) => setTimeout(r, interval));
    }
  }
  return false;
}

/** 通过 Tauri invoke + event 实现流式输出 */
export async function sendMessageStream(
  task: string,
  onChunk: (text: string) => void,
  onDone: () => void
): Promise<void> {
  log("debug", `sendMessageStream: task="${task.slice(0, 50)}..."`);

  // 监听 event 流
  let accumulated = "";
  const unlistenChunk = await listen<string>("task-chunk", (event) => {
    accumulated += event.payload;
    onChunk(accumulated);
  });
  const unlistenDone = await listen<string>("task-done", () => {
    unlistenChunk();
    unlistenDone();
    onDone();
  });

  try {
    log("debug", `[invoke] send_task: "${task.slice(0, 50)}..."`);
    await invoke("send_task", { task });
  } catch (e: any) {
    log("error", `sendMessageStream: ${e.message || e}`);
    onChunk(`\n\n错误: ${e.message || e}`);
    unlistenChunk();
    unlistenDone();
    onDone();
  }
}

export async function getGatewayStatus(): Promise<any> {
  const resp = await fetch(`${GATEWAY_URL}/api/status`);
  return resp.json();
}

export async function getSessions(): Promise<Session[]> {
  const resp = await fetch(`${GATEWAY_URL}/api/sessions`);
  const data = await resp.json();
  return data.sessions || [];
}

// ── P2: Agent 树可视化 ──

export interface AgentTreeNode {
  name: string;
  path: string;
  status: string;
  children: AgentTreeNode[];
  /** 当前 LLM 调用链 */
  current_llm_call?: string;
  tool_calls?: number;
  token_usage?: { prompt: number; completion: number; total: number };
  duration?: number;
}

export async function getAgentTree(): Promise<AgentTreeNode | null> {
  try {
    const resp = await fetch(`${GATEWAY_URL}/api/agent/tree`, { signal: AbortSignal.timeout(5000) });
    if (!resp.ok) return null;
    return resp.json();
  } catch { return null; }
}

// ── P2: 技能管理器 ──

export interface SkillInfo {
  name: string;
  description: string;
  enabled: boolean;
  category?: string;
}

export async function getSkills(): Promise<SkillInfo[]> {
  try {
    const resp = await fetch(`${GATEWAY_URL}/api/skills`, { signal: AbortSignal.timeout(5000) });
    if (!resp.ok) return [];
    const data = await resp.json();
    return data.skills || [];
  } catch { return []; }
}

export async function toggleSkill(name: string, enabled: boolean): Promise<boolean> {
  try {
    const resp = await fetch(`${GATEWAY_URL}/api/skills/toggle`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, enabled }),
    });
    return resp.ok;
  } catch { return false; }
}

// ── P2: Cron 任务管理器 ──

export interface CronJob {
  id: string;
  name: string;
  schedule: string;
  enabled: boolean;
  last_run?: string;
  next_run?: string;
  result?: string;
}

export async function getCronJobs(): Promise<CronJob[]> {
  try {
    const resp = await fetch(`${GATEWAY_URL}/api/cron`, { signal: AbortSignal.timeout(5000) });
    if (!resp.ok) return [];
    const data = await resp.json();
    return data.jobs || [];
  } catch { return []; }
}

export async function createCronJob(name: string, schedule: string, prompt: string): Promise<boolean> {
  try {
    const resp = await fetch(`${GATEWAY_URL}/api/cron/create`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, schedule, prompt }),
    });
    return resp.ok;
  } catch { return false; }
}

export async function deleteCronJob(id: string): Promise<boolean> {
  try {
    const resp = await fetch(`${GATEWAY_URL}/api/cron/remove`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    return resp.ok;
  } catch { return false; }
}

export async function toggleCronJob(id: string, enabled: boolean): Promise<boolean> {
  try {
    const endpoint = enabled ? "start" : "stop";
    const resp = await fetch(`${GATEWAY_URL}/api/cron/${endpoint}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    return resp.ok;
  } catch { return false; }
}

// ── P2: 审批通知 ──

export interface ApprovalRequest {
  id: string;
  command: string;
  detail: string;
  timestamp: number;
}

export async function getPendingApprovals(): Promise<ApprovalRequest[]> {
  try {
    const resp = await fetch(`${GATEWAY_URL}/api/approval/pending`, { signal: AbortSignal.timeout(5000) });
    if (!resp.ok) return [];
    const data = await resp.json();
    return data.pending || [];
  } catch { return []; }
}

export async function approveRequest(id: string): Promise<boolean> {
  try {
    const resp = await fetch(`${GATEWAY_URL}/api/approval/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    return resp.ok;
  } catch { return false; }
}

export async function denyRequest(id: string): Promise<boolean> {
  try {
    const resp = await fetch(`${GATEWAY_URL}/api/approval/deny`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id }),
    });
    return resp.ok;
  } catch { return false; }
}
