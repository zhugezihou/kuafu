<script lang="ts">
  import { loadConfig, saveConfig, type AppConfig } from "../lib/config";
  import { onMount } from "svelte";

  let {
    onClose = () => {},
  }: { onClose: () => void } = $props();

  let config = $state<AppConfig>({
    modelType: "local",
    localModelPath: "",
    localLlmEndpoint: "http://localhost:8080",
    cloudApiKey: "",
    cloudModel: "deepseek-chat",
    theme: "dark",
  });
  let saving = $state(false);
  let saved = $state(false);

  onMount(async () => {
    config = await loadConfig();
  });

  async function handleSave() {
    saving = true;
    saved = false;
    await saveConfig(config);
    // 应用主题
    document.documentElement.setAttribute("data-theme", config.theme);
    saving = false;
    saved = true;
    setTimeout(() => (saved = false), 2000);
  }

  function toggleTheme() {
    config.theme = config.theme === "dark" ? "light" : "dark";
  }
</script>

<div class="settings-overlay" onclick={onClose} role="presentation">
  <div class="settings-panel" onclick={(e) => e.stopPropagation()} role="dialog" aria-label="设置">
    <div class="settings-header">
      <h2>⚙ 设置</h2>
      <button class="close-btn" onclick={onClose}>✕</button>
    </div>

    <div class="settings-body">
      <!-- 模型选择 -->
      <section>
        <h3>模型</h3>
        <div class="field">
          <label>运行模式</label>
          <div class="toggle-group">
            <button
              class="toggle-btn"
              class:active={config.modelType === "local"}
              onclick={() => (config.modelType = "local")}
            >本地模型</button>
            <button
              class="toggle-btn"
              class:active={config.modelType === "cloud"}
              onclick={() => (config.modelType = "cloud")}
            >云端 API</button>
          </div>
        </div>

        {#if config.modelType === "local"}
          <div class="field">
            <label>模型路径 (GGUF)</label>
            <input
              type="text"
              bind:value={config.localModelPath}
              placeholder="/path/to/model.gguf"
            />
          </div>
          <div class="field">
            <label>LLM 端点</label>
            <input
              type="text"
              bind:value={config.localLlmEndpoint}
              placeholder="http://localhost:8080"
            />
          </div>
        {:else}
          <div class="field">
            <label>API Key</label>
            <input
              type="password"
              bind:value={config.cloudApiKey}
              placeholder="sk-..."
            />
          </div>
          <div class="field">
            <label>模型名称</label>
            <input
              type="text"
              bind:value={config.cloudModel}
              placeholder="deepseek-chat"
            />
          </div>
        {/if}
      </section>

      <!-- 主题 -->
      <section>
        <h3>主题</h3>
        <div class="field">
          <label>外观</label>
          <button class="toggle-btn" onclick={toggleTheme}>
            {config.theme === "dark" ? "🌙 深色" : "☀️ 浅色"}
          </button>
        </div>
      </section>
    </div>

    <div class="settings-footer">
      <span class="save-msg">{saved ? "✅ 已保存" : ""}</span>
      <button class="save-btn" onclick={handleSave} disabled={saving}>
        {saving ? "保存中..." : "保存"}
      </button>
    </div>
  </div>
</div>

<style>
  .settings-overlay {
    position: fixed;
    inset: 0;
    background: rgba(0, 0, 0, 0.4);
    display: flex;
    align-items: center;
    justify-content: center;
    z-index: 100;
  }

  .settings-panel {
    width: 460px;
    max-height: 80vh;
    background: var(--surface);
    border-radius: 12px;
    display: flex;
    flex-direction: column;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.3);
  }

  .settings-header {
    display: flex;
    align-items: center;
    padding: 16px 20px;
    border-bottom: 1px solid var(--border);
  }

  .settings-header h2 {
    flex: 1;
    margin: 0;
    font-size: 16px;
  }

  .close-btn {
    background: none;
    font-size: 16px;
    padding: 2px 8px;
  }

  .settings-body {
    flex: 1;
    overflow-y: auto;
    padding: 16px 20px;
  }

  section {
    margin-bottom: 20px;
  }

  h3 {
    font-size: 13px;
    margin: 0 0 10px;
    color: var(--text2);
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  .field {
    margin-bottom: 12px;
  }

  .field label {
    display: block;
    font-size: 12px;
    margin-bottom: 4px;
    color: var(--text2);
  }

  .field input {
    width: 100%;
    padding: 8px 10px;
    font-size: 13px;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 6px;
    color: var(--text1);
    box-sizing: border-box;
  }

  .toggle-group {
    display: flex;
    gap: 6px;
  }

  .toggle-btn {
    padding: 6px 14px;
    font-size: 12px;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 6px;
    cursor: pointer;
  }

  .toggle-btn.active {
    background: var(--accent);
    color: #fff;
    border-color: var(--accent);
  }

  .settings-footer {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    gap: 10px;
    padding: 12px 20px;
    border-top: 1px solid var(--border);
  }

  .save-msg {
    font-size: 12px;
    color: #22c55e;
  }

  .save-btn {
    padding: 6px 20px;
    font-size: 13px;
    background: var(--accent);
    color: #fff;
    border: none;
    border-radius: 6px;
    cursor: pointer;
  }

  .save-btn:disabled {
    opacity: 0.6;
  }
</style>
