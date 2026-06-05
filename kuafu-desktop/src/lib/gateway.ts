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

/** 同步发送（简单任务） */
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

/** 流式发送：前端直连 Gateway */
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
      body: JSON.stringify({ task, mode: "standard", sync: false }),
      signal: controller.signal,
    });

    if (!resp.ok) {
      onChunk(`\n\n错误: HTTP ${resp.status}`);
      onDone();
      return;
    }

    const data = await resp.json();
    if (data.status === "accepted") {
      onChunk("任务已提交，正在执行...\n\n");
      await pollForResult(onChunk, onDone);
    } else {
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

/** 轮询等待结果 */
async function pollForResult(
  onChunk: (text: string) => void,
  onDone: () => void,
  maxRetries = 60
): Promise<void> {
  for (let i = 0; i < maxRetries; i++) {
    await new Promise((r) => setTimeout(r, 1000));
    try {
      const resp = await fetch(`${GATEWAY_URL}/api/status`);
      if (resp.ok && (await resp.json()).status === "ok") {
        onChunk("✅ 任务完成");
        onDone();
        return;
      }
    } catch {}
  }
  onChunk("\n\n⚠ 等待超时");
  onDone();
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
