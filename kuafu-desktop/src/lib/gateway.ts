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

/** 同步发送消息，等待夸父返回完整结果 */
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

/** 同步发送，一次性返回结果（夸父 Gateway 暂不支持 SSE 流式） */
export async function sendMessageStream(
  task: string,
  onChunk: (text: string) => void,
  onDone: () => void
): Promise<void> {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 120000);

  try {
    const resp = await fetch(`${GATEWAY_URL}/api/task`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task, mode: "standard", sync: true }),
      signal: controller.signal,
    });

    if (!resp.ok) {
      onChunk(`\n\n错误: HTTP ${resp.status}`);
      onDone();
      return;
    }

    const data = await resp.json();
    if (data.result) {
      // 一次性输出完整结果（夸父不支持逐 token 流式）
      onChunk(data.result);
    } else {
      onChunk(data.error || "(无输出)");
    }
    onDone();
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

export async function getGatewayStatus(): Promise<any> {
  const resp = await fetch(`${GATEWAY_URL}/api/status`);
  return resp.json();
}

export async function getSessions(): Promise<Session[]> {
  const resp = await fetch(`${GATEWAY_URL}/api/sessions`);
  const data = await resp.json();
  return data.sessions || [];
}
