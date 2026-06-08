/// <reference types="svelte" />

import { log } from "./debug";
import { invoke } from "@tauri-apps/api/core";

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

/** 通过 Tauri invoke 发送消息（一次性返回结果，不流式） */
export async function sendMessageStream(
  task: string,
  onChunk: (text: string) => void,
  onDone: () => void
): Promise<void> {
  log("debug", `sendMessageStream: task="${task.slice(0, 50)}..."`);
  try {
    log("debug", `[invoke] send_task: "${task.slice(0, 50)}..."`);
    const resp = await invoke("send_task", { task }) as string;
    log("info", `sendMessageStream: result ${resp.length} chars`);
    // 尝试解析 JSON，失败就当纯文本
    try {
      const data = JSON.parse(resp);
      if (data && data.result) {
        onChunk(data.result);
      } else {
        onChunk(data.error || resp);
      }
    } catch {
      onChunk(resp || "(无输出)");
    }
  } catch (e: any) {
    log("error", `sendMessageStream: ${e.message || e}`);
    onChunk(`\n\n错误: ${e.message || e}`);
  }
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
