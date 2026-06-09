<script lang="ts">
  import { onMount } from "svelte";
  import { getPendingApprovals, approveRequest, denyRequest, type ApprovalRequest } from "../lib/gateway";
  import { log } from "../lib/debug";

  let pending = $state<ApprovalRequest[]>([]);
  let loading = $state(true);

  onMount(async () => {
    pending = await getPendingApprovals();
    loading = false;
  });

  async function refresh() {
    loading = true;
    pending = await getPendingApprovals();
    loading = false;
  }

  async function handleApprove(id: string) {
    const ok = await approveRequest(id);
    if (ok) {
      pending = pending.filter(r => r.id !== id);
      log("info", `approve: ${id}`);
    }
  }

  async function handleDeny(id: string) {
    const ok = await denyRequest(id);
    if (ok) {
      pending = pending.filter(r => r.id !== id);
      log("info", `deny: ${id}`);
    }
  }

  function formatTime(ts?: number): string {
    if (!ts) return "";
    const d = new Date(ts);
    return `${d.getHours().toString().padStart(2, "0")}:${d.getMinutes().toString().padStart(2, "0")}:${d.getSeconds().toString().padStart(2, "0")}`;
  }
</script>

<div class="approval-panel">
  <div class="panel-header">
    <span class="panel-title">🔐 待审批</span>
    <div class="panel-actions">
      <button class="refresh-btn" onclick={refresh} disabled={loading}>⟳</button>
    </div>
  </div>

  <div class="approval-list">
    {#if loading}
      <div class="empty">加载中…</div>
    {:else if pending.length === 0}
      <div class="empty">✅ 无待审批请求</div>
    {/if}
    {#each pending as req (req.id)}
      <div class="approval-item">
        <div class="req-header">
          <span class="req-time">{formatTime(req.timestamp)}</span>
          <span class="req-id">#{req.id.slice(0, 8)}</span>
        </div>
        <div class="req-command">{req.command}</div>
        {#if req.detail}
          <div class="req-detail" title={req.detail}>{req.detail.slice(0, 200)}</div>
        {/if}
        <div class="req-actions">
          <button class="approve-btn" onclick={() => handleApprove(req.id)}>✓ 允许</button>
          <button class="deny-btn" onclick={() => handleDeny(req.id)}>✕ 拒绝</button>
        </div>
      </div>
    {/each}
  </div>
</div>

<style>
  .approval-panel {
    display: flex;
    flex-direction: column;
    height: 100%;
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
    font-size: 11px; padding: 2px 8px; background: none;
    border: 1px solid var(--border); border-radius: 4px; cursor: pointer;
  }
  .approval-list { flex: 1; overflow-y: auto; }
  .approval-item {
    padding: 10px 12px;
    border-bottom: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .approval-item:hover { background: var(--surface2); }
  .req-header { display: flex; gap: 8px; font-size: 10px; color: var(--text2); }
  .req-command {
    font-family: var(--mono, monospace);
    font-size: 13px;
    padding: 4px 8px;
    background: var(--bg);
    border-radius: 4px;
    overflow-x: auto;
    white-space: nowrap;
  }
  .req-detail { font-size: 11px; color: var(--text2); }
  .req-actions { display: flex; gap: 6px; margin-top: 4px; }
  .approve-btn, .deny-btn {
    font-size: 12px; padding: 4px 14px; border-radius: 4px; cursor: pointer;
    border: none;
  }
  .approve-btn { background: #22c55e; color: #fff; }
  .approve-btn:hover { background: #16a34a; }
  .deny-btn { background: #ef4444; color: #fff; }
  .deny-btn:hover { background: #dc2626; }
  .empty { padding: 24px; text-align: center; color: var(--text2); font-size: 13px; }
</style>
