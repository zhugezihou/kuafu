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
  const unsub = messages.subscribe((m) => (msgs = m));
  unsub();
  try {
    localStorage.setItem(CURRENT_SESSION_KEY, JSON.stringify(msgs));
  } catch {}  // localStorage 满或不可用时静默失败
}

// 存档当前会话（切换到新会话时调用）
export function archiveCurrentSession() {
  let msgs: Message[] = [];
  const unsub1 = messages.subscribe((m) => (msgs = m));
  unsub1();

  if (msgs.length === 0) return;

  const archives = loadArchives();
  const title = extractTitle(msgs);

  // 检查是否有同 ID 的存档
  let sid: string = genId();
  const unsub2 = currentSessionId.subscribe((id) => { if (id) sid = id; });
  unsub2();
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

// 重命名存档会话
export function renameArchivedSession(id: string, newTitle: string) {
  const archives = loadArchives();
  const entry = archives.find((a) => a.id === id);
  if (entry) {
    entry.title = newTitle;
    saveArchives(archives);
    archivedSessions.set(archives);
  }
}

// ── 消息操作 ──

// 追加消息
export function addMessage(msg: Message) {
  messages.update((msgs) => [...msgs, { ...msg, timestamp: msg.timestamp || Date.now() }]);
  // 每次添加消息自动保存
  saveMessages();
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
  // 流式追加也自动保存
  saveMessages();
}

// 保存当前消息到 localStorage（每次变更自动触发）
let saveTimer: ReturnType<typeof setTimeout> | null = null;
function saveMessages() {
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    let msgs: Message[] = [];
    const unsub = messages.subscribe((m) => (msgs = m));
    unsub();
    try {
      localStorage.setItem(CURRENT_SESSION_KEY, JSON.stringify(msgs));
    } catch {}
  }, 300); // 300ms 防抖
}

// 清空当前会话（存档后再清）
export function clearMessages() {
  archiveCurrentSession();
  messages.set([]);
  const newId = genId();
  currentSessionId.set(newId);
  // 清空当前会话时也保存空的会话状态
  try {
    localStorage.setItem(CURRENT_SESSION_KEY, JSON.stringify([]));
  } catch {}
}
