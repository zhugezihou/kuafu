<script lang="ts">
  import { onMount } from "svelte";
  import {
    checkLocalEngines,
    startLlamaServer,
    stopLlamaServer,
    type LocalEngineStatus,
    type ModelFile,
  } from "../lib/gateway";
  import { log } from "../lib/debug";

  let status = $state<LocalEngineStatus | null>(null);
  let loading = $state(true);
  let message = $state("");

  // 模型参数配置
  let selectedModel = $state<string>("");
  let contextLength = $state(32768);
  let gpuLayers = $state(999);

  onMount(async () => {
    await refresh();
  });

  async function refresh() {
    loading = true;
    status = await checkLocalEngines();
    loading = false;
  }

  async function handleStart() {
    if (!selectedModel) { message = "请先选择一个模型"; return; }
    message = "正在启动 llama-server...";
    const result = await startLlamaServer(selectedModel, contextLength, gpuLayers);
    message = result;
    log("info", `startLlamaServer: ${result}`);
    setTimeout(refresh, 2000);
  }

  async function handleStop() {
    message = "正在停止...";
    const result = await stopLlamaServer();
    message = result;
    log("info", `stopLlamaServer: ${result}`);
    setTimeout(refresh, 1000);
  }

  function formatSize(mb: number): string {
    if (mb > 1024) return `${(mb / 1024).toFixed(1)} GB`;
    return `${mb.toFixed(0)} MB`;
  }

  function formatQuant(q?: string): string {
    if (!q) return "";
    const map: Record<string, string> = {
      "Q4_K_M": "Q4", "Q4_K_S": "Q4", "Q5_K_M": "Q5", "Q5_K_S": "Q5",
      "Q6_K": "Q6", "Q8_0": "Q8", "F16": "F16",
    };
    return map[q] || q;
  }
</script>

<div class="model-manager">
  <div class="panel-header">
    <span class="panel-title">🧠 本地模型管理</span>
    <button class="refresh-btn" onclick={refresh} disabled={loading}>⟳</button>
  </div>

  {#if loading}
    <div class="status-section"><div class="empty">检测中…</div></div>
  {:else if !status}
    <div class="status-section"><div class="empty">检测失败</div></div>
  {:else}
    <!-- 引擎状态 -->
    <div class="status-section">
      <div class="engine-row">
        <span class="engine-name">llama-server</span>
        <span class="engine-badge" class:installed={status.llama_server} class:missing={!status.llama_server}>
          {status.llama_server ? "已安装" : "未安装"}
        </span>
        <span class="engine-badge" class:running={status.llama_server_running} class:stopped={!status.llama_server_running}>
          {status.llama_server_running ? "● 运行中" : "○ 已停止"}
        </span>
      </div>
      <div class="engine-row">
        <span class="engine-name">Ollama</span>
        <span class="engine-badge" class:installed={status.ollama} class:missing={!status.ollama}>
          {status.ollama ? "已安装" : "未安装"}
        </span>
        <span class="engine-badge" class:running={status.ollama_running} class:stopped={!status.ollama_running}>
          {status.ollama_running ? "● 运行中" : "○ 已停止"}
        </span>
      </div>
    </div>

    <!-- 模型列表 -->
    <div class="section-title">可用模型 ({status.models.length})</div>
    <div class="model-list">
      {#if status.models.length === 0}
        <div class="empty">暂无模型文件，请将 .gguf 文件放入 models/ 目录</div>
      {:else}
        <div class="model-radio-group">
          {#each status.models as model (model.path)}
            <label class="model-radio" class:selected={selectedModel === model.path}>
              <input type="radio" name="model" value={model.path} bind:group={selectedModel} />
              <span class="model-name">{model.name}</span>
              <span class="model-size">{formatSize(model.size_mb)}</span>
              {#if model.quant}
                <span class="model-quant">{formatQuant(model.quant)}</span>
              {/if}
            </label>
          {/each}
        </div>
      {/if}
    </div>

    <!-- 启动参数 -->
    <div class="section-title">启动参数</div>
    <div class="params-section">
      <div class="param-row">
        <label>上下文长度</label>
        <select bind:value={contextLength}>
          <option value={8192}>8K</option>
          <option value={16384}>16K</option>
          <option value={32768}>32K</option>
          <option value={65536}>64K</option>
          <option value={131072}>128K</option>
        </select>
      </div>
      <div class="param-row">
        <label>GPU 加速层数</label>
        <select bind:value={gpuLayers}>
          <option value={0}>CPU 模式 (0层)</option>
          <option value={16}>16 层</option>
          <option value={32}>32 层</option>
          <option value={64}>64 层</option>
          <option value={999}>全部 (999层)</option>
        </select>
      </div>
    </div>

    <!-- 操作按钮 -->
    <div class="actions">
      {#if status.llama_server_running}
        <button class="stop-btn" onclick={handleStop}>■ 停止</button>
      {:else}
        <button class="start-btn" onclick={handleStart} disabled={!selectedModel}>
          ▶ 启动
        </button>
      {/if}
    </div>

    {#if message}
      <div class="message">{message}</div>
    {/if}
  {/if}
</div>

<style>
  .model-manager {
    display: flex;
    flex-direction: column;
    height: 100%;
    font-size: 13px;
  }
  .panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  .panel-title { font-weight: 600; font-size: 14px; }
  .refresh-btn {
    background: none; border: 1px solid var(--border); border-radius: 4px;
    padding: 2px 8px; cursor: pointer; font-size: 11px;
  }
  .refresh-btn:disabled { opacity: 0.5; }

  .status-section { padding: 10px 12px; display: flex; flex-direction: column; gap: 6px; }
  .engine-row { display: flex; align-items: center; gap: 8px; }
  .engine-name { flex: 1; font-weight: 500; }
  .engine-badge {
    font-size: 10px; padding: 2px 8px; border-radius: 4px;
    background: var(--surface2);
  }
  .engine-badge.installed { color: #22c55e; }
  .engine-badge.missing { color: #ef4444; }
  .engine-badge.running { color: #22c55e; }
  .engine-badge.stopped { color: var(--text2); }

  .section-title {
    font-size: 11px; font-weight: 600; color: var(--text2);
    padding: 6px 12px; text-transform: uppercase; letter-spacing: 0.5px;
    border-top: 1px solid var(--border);
  }

  .model-list { flex: 1; overflow-y: auto; }
  .model-radio-group { display: flex; flex-direction: column; }
  .model-radio {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 6px 12px;
    cursor: pointer;
    border-bottom: 1px solid var(--border);
  }
  .model-radio:hover { background: var(--surface2); }
  .model-radio.selected { background: var(--surface2); }
  .model-radio input { margin: 0; }
  .model-name { flex: 1; overflow: hidden; text-overflow: ellipsis; font-size: 12px; }
  .model-size { font-size: 10px; color: var(--text2); flex-shrink: 0; }
  .model-quant {
    font-size: 10px; padding: 1px 4px; border-radius: 3px;
    background: var(--accent); color: #fff; flex-shrink: 0;
  }

  .params-section { padding: 8px 12px; display: flex; flex-direction: column; gap: 6px; }
  .param-row { display: flex; align-items: center; gap: 8px; }
  .param-row label { flex: 1; font-size: 12px; }
  .param-row select {
    padding: 3px 6px; font-size: 11px;
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 4px; color: var(--text);
  }

  .actions { padding: 8px 12px; }
  .start-btn, .stop-btn {
    width: 100%; padding: 8px; border-radius: 6px;
    font-size: 13px; font-weight: 600; cursor: pointer;
    border: none;
  }
  .start-btn { background: var(--accent); color: #fff; }
  .start-btn:disabled { opacity: 0.4; cursor: not-allowed; }
  .stop-btn { background: #ef4444; color: #fff; }

  .message {
    margin: 0 12px 10px; padding: 6px 10px; font-size: 11px;
    background: var(--surface2); border-radius: 4px;
    text-align: center;
  }
  .empty { padding: 20px; text-align: center; color: var(--text2); font-size: 12px; }
</style>
