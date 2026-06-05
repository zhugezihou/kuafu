/// <reference types="svelte" />

// Gateway API 客户端 — 前端直连 localhost:8081
const GATEWAY_URL = "http://localhost:8081";

export interface Message {
  role: "user" | "assistant" | "tool" | "system";
  content: string;
  id?: string;
  timestamp?: number;
}

export interface Session {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  message_count: number;
}

export interface AgentStatus {
  status: string;
  version: string;
  model: string;
  backend: string;
  evolution: { total: number };
}

// ── 同步发送（简单任务，非流式） ──

export async function sendMessage(
  task: string,
  mode = "standard"
): Promise<string> {
  const resp = await fetch(`${GATEWAY_URL}/api/task`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task, mode, sync: true }),
  });
  const data = await resp.json();
  return data.result || data.error || "(无输出)";
}

/** 流式发送：前端直连 Gateway SSE，不再走 Tauri invoke 转发 */
export async function sendMessageStream(
  task: string,
  onChunk: (text: string) => void,
  onDone: () => void
): Promise<void> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 120000); // 2分钟超时

  try {
    const resp = await fetch(`${GATEWAY_URL}/api/task`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task, mode: "standard", sync: false }),
      signal: controller.signal,
    });

    if (!resp.ok) {
      onChunk(`\n\n错误: HTTP ${resp.status}`);
      onDone();
      return;
    }

    // 异步模式返回 202，没有 SSE 流——直接等结果
    // Gateway 异步模式下是后台线程执行，结果不可直接读取
    // 所以这里用轮询方式等待完成
    const data = await resp.json();
    if (data.status === "accepted") {
      // 异步提交成功，开始轮询最新消息
      onChunk("任务已提交，正在执行...\n\n");
      await pollForResult(task, onChunk, onDone);
    } else {
      // 同步模式
      onChunk(data.result || "");
      onDone();
    }
  } catch (e: any) {
    if (e.name === "AbortError") {
      onChunk("\n\n错误: 请求超时");
    } else {
      onChunk(`\n\n错误: ${e.message}`);
    }
    onDone();
  } finally {
    clearTimeout(timeout);
  }
}

/** 轮询等待结果（因为 Gateway 异步模式没有 SSE） */
async function pollForResult(
  task: string,
  onChunk: (text: string) => void,
  onDone: () => void,
  maxRetries = 60
): Promise<void> {
  // 用 status 接口判断 agent 是否完成了任务
  for (let i = 0; i < maxRetries; i++) {
    await new Promise((r) => setTimeout(r, 1000));
    try {
      const resp = await fetch(`${GATEWAY_URL}/api/status`);
      if (resp.ok) {
        const statusData = await resp.json();
        if (statusData.status === "ok") {
          onChunk("✅ 任务完成");
          onDone();
          return;
        }
      }
    } catch {
      // gateway 可能暂时不可用，继续轮询
    }
  }
  onChunk("\n\n⚠ 等待超时");
  onDone();
}

export async function getStatus(): Promise<any> {
  const resp = await fetch(`${GATEWAY_URL}/api/status`);
  return resp.json();
}

export async function getSessions(): Promise<Session[]> {
  const resp = await fetch(`${GATEWAY_URL}/api/sessions`);
  const data = await resp.json();
  return data.sessions || [];
}

// ── SSE 事件流 ──

export function connectSSE(
  onMessage: (text: string) => void,
  onStatus: (status: AgentStatus) => void
): () => void {
  const events = new EventSource(`${GATEWAY_URL}/api/events`);

  events.onmessage = (e) => {
    try {
      const data = JSON.parse(e.data);
      if (data.type === "message") onMessage(data.content);
      if (data.type === "status") onStatus(data);
    } catch {}
  };

  events.onerror = () => {};

  return () => events.close();
}
