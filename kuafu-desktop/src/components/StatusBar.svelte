<script lang="ts">
  import { isRunning, agentRunning, agentError } from "../lib/store";
  import { onMount } from "svelte";

  let version = $state("?");

  onMount(async () => {
    // 从 Tauri 获取版本号
    try {
      const { getVersion } = await import("@tauri-apps/api/app");
      version = await getVersion();
    } catch {
      version = "?";
    }
  });

  function toggleDebug() {
    // dispatch 自定义事件让父组件切换调试面板
    const event = new CustomEvent('debug-toggle');
    window.dispatchEvent(event);
  }
</script>

<footer class="status-bar">
  <div class="status-left">
    {#if $isRunning}
      <span class="running"><span class="spinner"></span> 思考中…</span>
    {:else if $agentError}
      <span class="error">⚠ {$agentError}</span>
    {:else if $agentRunning}
      <span class="idle">● 就绪</span>
    {:else}
      <span class="offline">○ 离线</span>
    {/if}
  </div>

  <div class="status-right">
    <button class="debug-btn" onclick={toggleDebug} title="调试日志 (Ctrl+Shift+D)">🐛</button>
    <span class="version">夸父 Desktop v{version}</span>
  </div>
</footer>

<style>
  .status-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 4px 14px;
    background: var(--surface);
    border-top: 1px solid var(--border);
    font-size: 11px;
    color: var(--text2);
    flex-shrink: 0;
  }

  .status-left,
  .status-right {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .running { color: var(--accent); display: flex; align-items: center; gap: 6px; }
  .spinner {
    display: inline-block; width: 10px; height: 10px;
    border: 2px solid var(--accent); border-top-color: transparent;
    border-radius: 50%; animation: spin 0.8s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  .idle { color: #22c55e; }
  .error { color: #ef4444; }
  .offline { color: #6b7280; }

  .debug-btn {
    background: none;
    border: 1px solid var(--border, #2a2a4a);
    color: #888;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 12px;
    cursor: pointer;
  }
  .debug-btn:hover { background: rgba(255,255,255,0.08); color: #ccc; }
</style>
