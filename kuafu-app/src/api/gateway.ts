// 夸父 Gateway API 客户端
import { Platform } from 'react-native';

const DEFAULT_HOST = 'http://127.0.0.1:8080';

let _baseUrl = DEFAULT_HOST;

export function setBaseUrl(url: string) {
  _baseUrl = url.replace(/\/+$/, '');
}

export function getBaseUrl(): string {
  return _baseUrl;
}

export interface StatusResponse {
  success: boolean;
  name?: string;
  version?: string;
  model?: string;
  backend?: string;
  task_count?: number;
}

export interface ChatResponse {
  success: boolean;
  message?: string;
  summary?: string;
  turns?: number;
  duration?: number;
  errors?: string[];
  model?: string;
}

export interface PendingApproval {
  id: string;
  title: string;
  detail: string;
  risk: string;
  tool: string;
  created_at: number;
}

// ── HTTP API ──

async function _fetch(path: string, options?: RequestInit): Promise<any> {
  const url = `${_baseUrl}${path}`;
  const res = await fetch(url, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`HTTP ${res.status}: ${text.slice(0, 200)}`);
  }
  return res.json();
}

export async function getStatus(): Promise<StatusResponse> {
  return _fetch('/api/status');
}

export async function sendMessage(text: string, sysPromptExtra?: string): Promise<ChatResponse> {
  return _fetch('/api/chat', {
    method: 'POST',
    body: JSON.stringify({ text, system_prompt_extra: sysPromptExtra || undefined }),
  });
}

export async function resetConversation(): Promise<{ success: boolean }> {
  return _fetch('/api/reset', { method: 'POST' });
}

export async function approveRequest(reqId: string): Promise<{ success: boolean }> {
  return _fetch('/api/approve', {
    method: 'POST',
    body: JSON.stringify({ req_id: reqId }),
  });
}

export async function rejectRequest(reqId: string): Promise<{ success: boolean }> {
  return _fetch('/api/reject', {
    method: 'POST',
    body: JSON.stringify({ req_id: reqId }),
  });
}

export async function getPendingApprovals(): Promise<{ success: boolean; approvals: PendingApproval[] }> {
  return _fetch('/api/approvals/pending');
}

export async function switchModel(target: string): Promise<{ success: boolean; message: string }> {
  return _fetch('/api/model', {
    method: 'POST',
    body: JSON.stringify({ target }),
  });
}

// ── SSE 连接 ──

export type SSEEvent = {
  type: 'connected' | 'llm_start' | 'llm_end' | 'tool_start' | 'tool_end' | 'approval_request' | 'approval_result';
  [key: string]: any;
};

export function connectSSE(onEvent: (event: SSEEvent) => void): () => void {
  // React Native 无原生 EventSource，使用 polling fallback
  return startSSEPolling(onEvent);
}

// Polling-based SSE fallback for React Native
export function startSSEPolling(
  onEvent: (event: SSEEvent) => void,
  interval: number = 3000,
): () => void {
  let lastStatus: any = null;
  let timer: ReturnType<typeof setInterval> | null = null;
  let running = true;

  async function poll() {
    if (!running) return;
    try {
      const status = await getStatus();
      if (!lastStatus) {
        lastStatus = status;
        onEvent({ type: 'connected' });
        return;
      }

      // Detect changes in task count → indicates new activity
      if (status.task_count !== lastStatus.task_count) {
        onEvent({ type: 'llm_start', turn: status.task_count, ts: Date.now() / 1000 });
        // We don't know when it ends, but the chat response gives us the final result
      }
      lastStatus = status;
    } catch {}
  }

  poll();
  timer = setInterval(poll, interval);

  return () => {
    running = false;
    if (timer) clearInterval(timer);
  };
}
