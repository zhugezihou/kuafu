// Svelte stores
import { writable } from "svelte/store";

export const messages = writable<Message[]>([]);
export const sessions = writable<Session[]>([]);
export const currentSessionId = writable<string | null>(null);
export const isRunning = writable(false);
export const agentStatus = writable<AgentStatus | null>(null);

// 追加消息
export function addMessage(msg: Message) {
  messages.update((msgs) => [...msgs, msg]);
}

// 清空当前会话
export function clearMessages() {
  messages.set([]);
}
