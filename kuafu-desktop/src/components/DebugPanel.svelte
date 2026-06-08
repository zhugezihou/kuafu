<script lang="ts">
  import { getLogs, clearLogs } from "../lib/debug";

  let visible = $state(false);
  let logs = $state<Array<{ time: string; level: string; msg: string }>>([]);
  let filter = $state<string>("all");
  let autoScroll = $state(true);
  let logContainer: HTMLDivElement | undefined;

  // 每秒刷新日志
  let timer: ReturnType<typeof setInterval> | undefined;

  $effect(() => {
    if (visible) {
      timer = setInterval(() => {
        logs = getLogs();
      }, 500);
    } else {
      if (timer) clearInterval(timer);
    }
    return () => { if (timer) clearInterval(timer); };
  });

  // 自动滚动到底部
  $effect(() => {
    if (autoScroll && logContainer) {
      logContainer.scrollTop = logContainer.scrollHeight;
    }
  });

  function filteredLogs() {
    if (filter === "all") return logs;
    return logs.filter(l => l.level === filter);
  }

  function toggle() {
    visible = !visible;
  }

  function handleClear() {
    clearLogs();
    logs = [];
  }

  function handleCopy() {
    const text = logs.map(l => `[${l.time}] [${l.level.toUpperCase()}] ${l.msg}`).join("\n");
    navigator.clipboard.writeText(text);
  }

  // 暴露 toggle 给父组件
  export { toggle };
</script>

{#if visible}
  <div class="debug-panel">
    <div class="debug-header">
      <span class="debug-title">🐛 调试日志</span>
      <div class="debug-actions">
        <select bind:value={filter} class="filter-select">
          <option value="all">全部</option>
          <option value="error">仅错误</option>
          <option value="warn">警告</option>
          <option value="info">信息</option>
          <option value="debug">调试</option>
        </select>
        <button class="debug-btn" onclick={handleClear}>清空</button>
        <button class="debug-btn" onclick={handleCopy}>复制</button>
        <label class="auto-scroll">
          <input type="checkbox" bind:checked={autoScroll} /> 自动滚动
        </label>
        <button class="debug-btn close-btn" onclick={toggle}>✕</button>
      </div>
    </div>
    <div class="log-list" bind:this={logContainer}>
      {#each filteredLogs() as entry}
        <div class="log-entry" class:log-error={entry.level === "error"} class:log-warn={entry.level === "warn"}>
          <span class="log-time">{entry.time}</span>
          <span class="log-level" class:level-error={entry.level === "error"} class:level-warn={entry.level === "warn"}>
            {entry.level.toUpperCase()}
          </span>
          <span class="log-msg">{entry.msg}</span>
        </div>
      {:else}
        <div class="log-empty">暂无日志</div>
      {/each}
    </div>
  </div>
{/if}

<style>
  .debug-panel {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    height: 250px;
    background: rgba(10, 10, 20, 0.95);
    border-top: 1px solid var(--border, #2a2a4a);
    z-index: 9999;
    display: flex;
    flex-direction: column;
    font-family: 'Cascadia Code', 'Consolas', monospace;
    font-size: 11px;
  }
  .debug-header {
    display: flex;
    align-items: center;
    padding: 6px 12px;
    background: rgba(255,255,255,0.03);
    border-bottom: 1px solid var(--border, #2a2a4a);
    flex-shrink: 0;
    gap: 8px;
  }
  .debug-title {
    font-weight: 600;
    font-size: 12px;
    color: #aaa;
    flex-shrink: 0;
  }
  .debug-actions {
    display: flex;
    align-items: center;
    gap: 6px;
    flex: 1;
    justify-content: flex-end;
  }
  .filter-select {
    background: rgba(255,255,255,0.08);
    border: 1px solid var(--border, #2a2a4a);
    color: #ccc;
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 11px;
    font-family: inherit;
  }
  .debug-btn {
    background: rgba(255,255,255,0.08);
    border: 1px solid var(--border, #2a2a4a);
    color: #aaa;
    padding: 2px 8px;
    border-radius: 4px;
    font-size: 11px;
    cursor: pointer;
    font-family: inherit;
  }
  .debug-btn:hover { background: rgba(255,255,255,0.15); }
  .close-btn { color: #ef4444; }
  .auto-scroll {
    display: flex;
    align-items: center;
    gap: 4px;
    color: #888;
    font-size: 11px;
  }
  .log-list {
    flex: 1;
    overflow-y: auto;
    padding: 4px 0;
  }
  .log-entry {
    display: flex;
    gap: 8px;
    padding: 2px 12px;
    line-height: 1.5;
  }
  .log-entry:hover { background: rgba(255,255,255,0.03); }
  .log-entry.log-error { background: rgba(239,68,68,0.08); }
  .log-entry.log-warn { background: rgba(245,158,11,0.08); }
  .log-time { color: #555; flex-shrink: 0; width: 70px; }
  .log-level { 
    flex-shrink: 0; width: 44px; font-weight: 600;
    color: #6af;
  }
  .level-error { color: #ef4444; }
  .level-warn { color: #f59e0b; }
  .log-msg { color: #aaa; white-space: pre-wrap; word-break: break-all; }
  .log-empty {
    color: #555; text-align: center; padding: 40px 0; font-size: 13px;
  }
</style>
