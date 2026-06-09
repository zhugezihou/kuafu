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
