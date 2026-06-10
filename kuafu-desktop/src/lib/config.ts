// 配置存储（localStorage 后备，避免 tauri-plugin-store 兼容问题）
import { writable } from "svelte/store";

export interface AppConfig {
  cloudProvider: "deepseek" | "openai" | "custom";
  cloudApiKey: string;
  cloudBaseUrl: string;
  cloudModel: string;
  theme: "dark" | "light";
  setupComplete: boolean;  // 是否已完成环境检测和初始配置
}

const STORAGE_KEY = "kuafu-desktop-config";

const defaults: AppConfig = {
  cloudProvider: "deepseek",
  cloudApiKey: "",
  cloudBaseUrl: "https://api.deepseek.com",
  cloudModel: "deepseek-chat",
  theme: "dark",
  setupComplete: false,
};

// 响应式 store
export const configStore = writable<AppConfig>(defaults);

export function loadConfig(): AppConfig {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      // 兼容旧配置：删除已废弃的本地模型字段
      const { modelType, localModelPath, localLlmEndpoint, localContextLength, localGpuLayers, ...clean } = parsed;
      return { ...defaults, ...clean };
    }
  } catch {}
  return { ...defaults };
}

export function saveConfig(config: AppConfig): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(config));
  } catch {}
}
