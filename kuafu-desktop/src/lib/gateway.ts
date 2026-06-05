/// <reference types="svelte" />

// Gateway API 客户端
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

// ── API 调用 ──

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

export async function getStatus(): Promise<AgentStatus> {
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
