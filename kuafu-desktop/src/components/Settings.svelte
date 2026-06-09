<script lang="ts">
  import { loadConfig, saveConfig, type AppConfig } from "../lib/config";

  let {
    onClose = () => {},
  }: { onClose: () => void } = $props();

  let config = $state<AppConfig>(loadConfig());
  let saving = $state(false);
  let saveMsg = $state("");

  async function handleSave() {
    saving = true;
    saveMsg = "";

    // 持久化到 localStorage
    saveConfig(config);

    // 应用主题
    document.documentElement.setAttribute("data-theme", config.theme);

    // 把配置传给 Rust 后端
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      await invoke("update_agent_config", {
        config: {
          model_type: config.modelType,
          local_model_path: config.localModelPath,
          local_llm_endpoint: config.localLlmEndpoint,
          local_context_length: config.localContextLength,
          local_gpu_layers: config.localGpuLayers,
          cloud_api_key: config.cloudApiKey,
          cloud_model: config.cloudModel,
        },
      });
      saveMsg = "✅ 配置已保存";
      await new Promise((r) => setTimeout(r, 500));

      // 如果引擎正在运行，自动重启让配置生效
      const status = await invoke("agent_status") as any;
      if (status.running) {
        saveMsg = "🔄 重启引擎...";
        await invoke("restart_agent");
        // 等待 gateway 就绪
        const { waitForGateway } = await import("../lib/gateway");
        const ready = await waitForGateway(15, 1000);
        if (ready) {
          saveMsg = "✅ 配置已生效";
        } else {
          saveMsg = "✅ 配置已保存（引擎重启中，请稍候）";
        }
      }
    } catch (e: any) {
      saveMsg = `⚠ 保存失败: ${e}`;
    } finally {
      saving = false;
      setTimeout(() => (saveMsg = ""), 3000);
    }
  }

  function toggleTheme() {
    config.theme = config.theme === "dark" ? "light" : "dark";
  }

  async function restartEngine() {
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      await invoke("restart_agent");
      saveMsg = "✅ 引擎已重启";
    } catch (e: any) {
      saveMsg = `⚠ 重启失败: ${e}`;
    }
  }
</script>

<div class="settings-overlay" onclick={onClose} role="presentation">
  <div class="settings-panel" onclick={(e) => e.stopPropagation()} role="dialog" aria-label="设置">
    <div class="settings-header">
      <h2>⚙ 设置</h2>
      <button class="close-btn" onclick={onClose}>✕</button>
    </div>

    <div class="settings-body">
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
            <label>LLM 端点</label>
            <input type="text" bind:value={config.localLlmEndpoint} placeholder="http://localhost:8080" />
          </div>
          <div class="field">
            <label>模型路径 (GGUF, 可选)</label>
            <input type="text" bind:value={config.localModelPath} placeholder="/path/to/model.gguf" />
          </div>
          <div class="field">
            <label>上下文长度 (context)</label>
            <select bind:value={config.localContextLength}>
              <option value={8192}>8K</option>
              <option value={16384}>16K</option>
              <option value={32768}>32K</option>
              <option value={65536}>64K</option>
              <option value={131072}>128K</option>
            </select>
          </div>
          <div class="field">
            <label>GPU 加速层数</label>
            <select bind:value={config.localGpuLayers}>
              <option value={0}>CPU 模式</option>
              <option value={16}>16 层</option>
              <option value={32}>32 层</option>
              <option value={64}>64 层</option>
              <option value={999}>全部</option>
            </select>
          </div>
        {:else}
          <div class="field">
            <label>API 提供商</label>
            <div class="toggle-group">
              <button
                class="toggle-btn"
                class:active={config.cloudProvider === "deepseek"}
                onclick={() => { config.cloudProvider = "deepseek"; config.cloudBaseUrl = "https://api.deepseek.com"; config.cloudModel = "deepseek-chat"; }}
              >DeepSeek</button>
              <button
                class="toggle-btn"
                class:active={config.cloudProvider === "openai"}
                onclick={() => { config.cloudProvider = "openai"; config.cloudBaseUrl = "https://api.openai.com/v1"; config.cloudModel = "gpt-4o-mini"; }}
              >OpenAI</button>
              <button
                class="toggle-btn"
                class:active={config.cloudProvider === "custom"}
                onclick={() => { config.cloudProvider = "custom"; }}
              >自定义</button>
            </div>
          </div>
          <div class="field">
            <label>API Key</label>
            <input type="password" bind:value={config.cloudApiKey} placeholder="sk-..." />
          </div>
          {#if config.cloudProvider === "custom"}
          <div class="field">
            <label>API 地址</label>
            <input type="text" bind:value={config.cloudBaseUrl} placeholder="https://api.example.com/v1" />
          </div>
          {/if}
          <div class="field">
            <label>模型名称</label>
            <input type="text" bind:value={config.cloudModel} placeholder="deepseek-chat" />
          </div>
        {/if}
      </section>

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
      <div class="save-msg">{saveMsg}</div>
      <button class="restart-btn" onclick={restartEngine}>重启引擎</button>
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
    gap: 8px;
    padding: 12px 20px;
    border-top: 1px solid var(--border);
  }

  .save-msg {
    flex: 1;
    font-size: 12px;
    color: var(--text2);
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
    opacity: 0.5;
    cursor: not-allowed;
  }

  .restart-btn {
    padding: 6px 14px;
    font-size: 12px;
    background: var(--surface2);
    border: 1px solid var(--border);
    border-radius: 6px;
    cursor: pointer;
    color: var(--text1);
  }

  .restart-btn:hover {
    background: var(--accent);
    color: #fff;
    border-color: var(--accent);
  }
</style>
