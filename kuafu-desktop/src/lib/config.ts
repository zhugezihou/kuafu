// 配置存储
import { Store } from "@tauri-apps/plugin-store";

export interface AppConfig {
  // 模型配置
  modelType: "local" | "cloud";
  localModelPath: string;
  localLlmEndpoint: string;
  cloudApiKey: string;
  cloudModel: string;
  // 主题
  theme: "dark" | "light";
}

const defaults: AppConfig = {
  modelType: "local",
  localModelPath: "",
  localLlmEndpoint: "http://localhost:8080",
  cloudApiKey: "",
  cloudModel: "deepseek-chat",
  theme: "dark",
};

let store: Store | null = null;
let cached: AppConfig | null = null;

export async function getStore(): Promise<Store> {
  if (!store) {
    store = await Store.load("config.json");
  }
  return store;
}

export async function loadConfig(): Promise<AppConfig> {
  if (cached) return cached;
  const s = await getStore();
  const cfg: AppConfig = { ...defaults };
  for (const key of Object.keys(defaults) as (keyof AppConfig)[]) {
    const val = await s.get(key);
    if (val !== undefined) (cfg as any)[key] = val;
  }
  cached = cfg;
  return cfg;
}

export async function saveConfig(config: AppConfig): Promise<void> {
  const s = await getStore();
  for (const [key, val] of Object.entries(config)) {
    await s.set(key, val);
  }
  await s.save();
  cached = config;
}
