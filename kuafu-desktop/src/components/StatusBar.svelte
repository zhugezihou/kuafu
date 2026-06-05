<script lang="ts">
  import { isRunning, agentRunning, agentError } from "../lib/store";
</script>

<footer class="status-bar">
  <div class="status-left">
    {#if $isRunning}
      <span class="running">⏳ 运行中…</span>
    {:else if $agentError}
      <span class="error">⚠ {$agentError}</span>
    {:else if $agentRunning}
      <span class="idle">● 就绪</span>
    {:else}
      <span class="offline">○ 离线</span>
    {/if}
  </div>

  <div class="status-right">
    <span class="version">夸父 Desktop v1.0.2</span>
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

  .running { color: var(--accent); }
  .idle { color: #22c55e; }
  .error { color: #ef4444; }
  .offline { color: #6b7280; }
</style>
