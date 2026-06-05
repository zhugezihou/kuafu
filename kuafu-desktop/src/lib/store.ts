import { writable } from "svelte/store";
import type { Message, Session } from "./gateway";

export interface AgentStatus {
  running: boolean;
  pid: number | null;
  gateway_port: number;
  python_path: string;
  error: string | null;
}

// 存档会话结构
export interface ArchivedSession {
  id: string;
  title: string;
  messages: Message[];
  updatedAt: number;
}

export const messages = writable<Message[]>([]);
export const sessions = writable<Session[]>([]);
export const archivedSessions = writable<ArchivedSession[]>([]);
export const currentSessionId = writable<string | null>(null);
export const isRunning = writable(false);
export const agentRunning = writable(false);
export const agentError = writable<string | null>(null);

const ARCHIVE_KEY = "kuafu-desktop-archives";
const CURRENT_SESSION_KEY = "kuafu-desktop-current-session";

// ── 多会话持久化 ──

function loadArchives(): ArchivedSession[] {
  try {
    const raw = localStorage.getItem(ARCHIVE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) return parsed;
    }
  } catch {}
  return [];
}

function saveArchives(archives: ArchivedSession[]) {
  try {
    // 只保留最近 20 个会话，避免 localStorage 溢出
    const trimmed = archives.slice(0, 20);
    localStorage.setItem(ARCHIVE_KEY, JSON.stringify(trimmed));
  } catch {}
}

// 生成唯一 ID
function genId(): string {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
}

// 从消息列表提取标题（取第一条用户消息）
function extractTitle(msgs: Message[]): string {
  for (const m of msgs) {
    if (m.role === "user") {
      const text = m.content.trim().slice(0, 30);
      return text.length < m.content.trim().length ? text + "..." : text;
    }
  }
  return "新对话";
}

// 加载当前会话
export function loadSession() {
  try {
    const raw = localStorage.getItem(CURRENT_SESSION_KEY);
    if (raw) {
      const msgs = JSON.parse(raw) as Message[];
      if (Array.isArray(msgs) && msgs.length > 0) {
        messages.set(msgs);
      }
    }
  } catch {}

  // 加载存档列表（用于 Sidebar 显示）
  archivedSessions.set(loadArchives());
}

// 保存当前会话到 localStorage
export function saveSession() {
  let msgs: Message[] = [];
  messages.subscribe((m) => (msgs = m))();
  try {
    localStorage.setItem(CURRENT_SESSION_KEY, JSON.stringify(msgs));
  } catch {}
}

// 存档当前会话（切换到新会话时调用）
export function archiveCurrentSession() {
  let msgs: Message[] = [];
  messages.subscribe((m) => (msgs = m))();

  if (msgs.length === 0) return;

  const archives = loadArchives();
  const title = extractTitle(msgs);

  // 检查是否有同 ID 的存档
  let sid: string;
  currentSessionId.subscribe((id) => (sid = id || genId()))();
  const idx = archives.findIndex((a) => a.id === sid);

  const entry: ArchivedSession = {
    id: sid!,
    title,
    messages: msgs,
    updatedAt: Date.now(),
  };

  if (idx >= 0) {
    archives[idx] = entry;
  } else {
    archives.unshift(entry);
  }

  saveArchives(archives);
  archivedSessions.set(archives);
  localStorage.removeItem(CURRENT_SESSION_KEY);
}

// 加载存档会话
export function loadArchivedSession(id: string) {
  const archives = loadArchives();
  const entry = archives.find((a) => a.id === id);
  if (entry) {
    messages.set(entry.messages);
    currentSessionId.set(id);
  }
}

// 删除存档会话
export function deleteArchivedSession(id: string) {
  const archives = loadArchives().filter((a) => a.id !== id);
  saveArchives(archives);
  archivedSessions.set(archives);
}

// ── 消息操作 ──

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

// 清空当前会话（存档后再清）
export function clearMessages() {
  archiveCurrentSession();
  messages.set([]);
  currentSessionId.set(genId());
}
