import { writable } from "svelte/store";
import type { Message, Session, AgentStatus } from "./gateway";

export const messages = writable<Message[]>([]);
export const sessions = writable<Session[]>([]);
export const currentSessionId = writable<string | null>(null);
export const isRunning = writable(false);
export const agentStatus = writable<AgentStatus | null>(null);

// 追加消息
export function addMessage(msg: Message) {
  messages.update((msgs) => [...msgs, msg]);
}

// 追加到最后一条助手消息（流式输出）
export function appendToLastAssistant(chunk: string) {
  messages.update((msgs) => {
    const last = msgs[msgs.length - 1];
    if (last && last.role === "assistant") {
      return [...msgs.slice(0, -1), { ...last, content: last.content + chunk }];
    }
    return [...msgs, { role: "assistant", content: chunk }];
  });
}

// 清空当前会话
export function clearMessages() {
  messages.set([]);
}
