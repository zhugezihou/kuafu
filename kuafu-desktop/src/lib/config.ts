// 配置存储（localStorage 后备，避免 tauri-plugin-store 兼容问题）
import { writable } from "svelte/store";

export interface AppConfig {
  modelType: "local" | "cloud";
  localModelPath: string;
  localLlmEndpoint: string;
  cloudProvider: "deepseek" | "openai" | "custom";
  cloudApiKey: string;
  cloudBaseUrl: string;
  cloudModel: string;
  theme: "dark" | "light";
}

const STORAGE_KEY = "kuafu-desktop-config";

const defaults: AppConfig = {
  modelType: "local",
  localModelPath: "",
  localLlmEndpoint: "http://localhost:8080",
  cloudProvider: "deepseek",
  cloudApiKey: "",
  cloudBaseUrl: "https://api.deepseek.com",
  cloudModel: "deepseek-chat",
  theme: "dark",
};

// 响应式 store
export const configStore = writable<AppConfig>(defaults);

export function loadConfig(): AppConfig {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const parsed = JSON.parse(raw);
      return { ...defaults, ...parsed };
    }
  } catch {}
  return { ...defaults };
}

export function saveConfig(config: AppConfig): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(config));
  } catch {}
}
