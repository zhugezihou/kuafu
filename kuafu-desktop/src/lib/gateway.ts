/// <reference types="svelte" />

import { log } from "./debug";

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
  log("debug", `sendMessage: task="${task.slice(0, 50)}..." mode=${mode}`);
  const resp = await fetch(`${GATEWAY_URL}/api/task`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ task, mode, sync: true }),
  });
  const data = await resp.json();
  log("info", `sendMessage: status=${resp.status} result_len=${(data.result || "").length}`);
  return data.result || data.error || "(无输出)";
}

/** 同步发送，一次性返回结果（夸父 Gateway 暂不支持 SSE 流式） */
export async function sendMessageStream(
  task: string,
  onChunk: (text: string) => void,
  onDone: () => void
): Promise<void> {
  log("debug", `sendMessageStream: task="${task.slice(0, 50)}..."`);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 120000);

  try {
    log("debug", `[HTTP] POST ${GATEWAY_URL}/api/task`);
    const resp = await fetch(`${GATEWAY_URL}/api/task`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task, mode: "standard", sync: true }),
      signal: controller.signal,
    });

    if (!resp.ok) {
      log("error", `sendMessageStream: HTTP ${resp.status} ${resp.statusText}`);
      onChunk(`\n\n错误: HTTP ${resp.status}`);
      onDone();
      return;
    }

    log("info", "sendMessageStream: HTTP 200");
    const data = await resp.json();
    if (data.result) {
      log("info", `sendMessageStream: result ${data.result.length} chars`);
      onChunk(data.result);
    } else {
      log("warn", `sendMessageStream: no result, error=${data.error || "(empty)"}`);
      onChunk(data.error || "(无输出)");
    }
    onDone();
  } catch (e: any) {
    log("error", `sendMessageStream: ${e.name === "AbortError" ? "超时" : e.message}`);
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
