<script lang="ts">
  import { agentStatus, isRunning } from "../lib/store";
</script>

<footer class="status-bar">
  <div class="status-left">
    {#if $isRunning}
      <span class="running">⏳ 运行中…</span>
    {:else}
      <span class="idle">● 就绪</span>
    {/if}
  </div>

  <div class="status-right">
    {#if $agentStatus}
      <span class="model">{$agentStatus.model}</span>
      <span class="backend">{$agentStatus.backend}</span>
    {/if}
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

  .running {
    color: var(--accent);
  }

  .idle {
    color: var(--success);
  }

  .model {
    background: var(--surface2);
    padding: 1px 6px;
    border-radius: 4px;
  }

  .backend {
    background: var(--surface2);
    padding: 1px 6px;
    border-radius: 4px;
  }
</style>
