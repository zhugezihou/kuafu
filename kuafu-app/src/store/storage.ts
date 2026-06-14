// 夸父 App — 消息持久化存储
import AsyncStorage from '@react-native-async-storage/async-storage';

const STORAGE_KEY = 'kuafu:messages';
const MAX_MESSAGES = 200;

export interface StoredMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
}

export async function loadMessages(): Promise<StoredMessage[]> {
  try {
    const raw = await AsyncStorage.getItem(STORAGE_KEY);
    if (raw) {
      return JSON.parse(raw);
    }
  } catch {}
  return [];
}

export async function saveMessages(messages: StoredMessage[]): Promise<void> {
  try {
    // 只保留最近的 MAX_MESSAGES 条
    const trimmed = messages.slice(-MAX_MESSAGES);
    await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(trimmed));
  } catch {}
}

export async function appendMessage(msg: StoredMessage): Promise<void> {
  const msgs = await loadMessages();
  msgs.push(msg);
  await saveMessages(msgs);
}

export async function updateMessage(id: string, updates: Partial<StoredMessage>): Promise<void> {
  const msgs = await loadMessages();
  const idx = msgs.findIndex(m => m.id === id);
  if (idx !== -1) {
    msgs[idx] = { ...msgs[idx], ...updates };
    await saveMessages(msgs);
  }
}

export async function clearMessages(): Promise<void> {
  try {
    await AsyncStorage.removeItem(STORAGE_KEY);
  } catch {}
}
