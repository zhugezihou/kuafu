<script lang="ts">
  import { onMount } from "svelte";
  import { getCronJobs, createCronJob, deleteCronJob, toggleCronJob, type CronJob } from "../lib/gateway";
  import { log } from "../lib/debug";

  let jobs = $state<CronJob[]>([]);
  let loading = $state(true);
  let showNew = $state(false);
  let newName = $state("");
  let newSchedule = $state("");
  let newPrompt = $state("");

  onMount(async () => {
    jobs = await getCronJobs();
    loading = false;
  });

  async function refresh() {
    loading = true;
    jobs = await getCronJobs();
    loading = false;
  }

  async function handleCreate() {
    if (!newName.trim() || !newSchedule.trim() || !newPrompt.trim()) return;
    const ok = await createCronJob(newName.trim(), newSchedule.trim(), newPrompt.trim());
    if (ok) {
      log("info", `createCronJob: ${newName}`);
      newName = ""; newSchedule = ""; newPrompt = "";
      showNew = false;
      await refresh();
    }
  }

  async function handleDelete(id: string) {
    const ok = await deleteCronJob(id);
    if (ok) {
      jobs = jobs.filter(j => j.id !== id);
      log("info", `deleteCronJob: ${id}`);
    }
  }

  async function handleToggle(job: CronJob) {
    const ok = await toggleCronJob(job.id, !job.enabled);
    if (ok) {
      jobs = jobs.map(j => j.id === job.id ? { ...j, enabled: !j.enabled } : j);
    }
  }
</script>

<div class="cron-panel">
  <div class="panel-header">
    <span class="panel-title">⏰ 定时任务</span>
    <div class="panel-actions">
      <button class="new-btn" onclick={() => (showNew = !showNew)}>
        {showNew ? "✕" : "＋ 新建"}
      </button>
      <button class="refresh-btn" onclick={refresh} disabled={loading}>⟳</button>
    </div>
  </div>

  {#if showNew}
    <div class="new-form">
      <input type="text" placeholder="任务名称" bind:value={newName} />
      <input type="text" placeholder="定时表达式 (如: 0 9 * * *, every 30m)" bind:value={newSchedule} />
      <textarea placeholder="任务提示词" bind:value={newPrompt} rows="3"></textarea>
      <div class="form-actions">
        <button class="save-btn" onclick={handleCreate} disabled={!newName.trim() || !newSchedule.trim() || !newPrompt.trim()}>创建</button>
        <button class="cancel-btn" onclick={() => (showNew = false)}>取消</button>
      </div>
    </div>
  {/if}

  <div class="job-list">
    {#if loading}
      <div class="empty">加载中…</div>
    {:else if jobs.length === 0}
      <div class="empty">暂无定时任务</div>
    {/if}
    {#each jobs as job (job.id)}
      <div class="job-item">
        <div class="job-header">
          <label class="job-toggle">
            <input type="checkbox" checked={job.enabled} onchange={() => handleToggle(job)} />
            <span class="job-name">{job.name || job.id}</span>
          </label>
          <span class="job-schedule">{job.schedule}</span>
          <button class="delete-btn" onclick={() => handleDelete(job.id)}>✕</button>
        </div>
        <div class="job-meta">
          {#if job.last_run}<span>上次: {job.last_run}</span>{/if}
          {#if job.next_run}<span>下次: {job.next_run}</span>{/if}
        </div>
        {#if job.result}
          <div class="job-result" title={job.result}>{job.result.slice(0, 100)}{job.result.length > 100 ? "…" : ""}</div>
        {/if}
      </div>
    {/each}
  </div>
</div>

<style>
  .cron-panel {
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
  .panel-actions { display: flex; gap: 6px; }
  .new-btn, .refresh-btn {
    font-size: 11px; padding: 2px 8px; background: none;
    border: 1px solid var(--border); border-radius: 4px; cursor: pointer;
  }
  .new-btn { background: var(--accent); color: #fff; border-color: var(--accent); }
  .new-form {
    padding: 10px 12px;
    display: flex;
    flex-direction: column;
    gap: 6px;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
  }
  .new-form input, .new-form textarea {
    padding: 4px 8px; font-size: 12px;
    background: var(--bg); border: 1px solid var(--border);
    border-radius: 4px; color: var(--text);
  }
  .new-form textarea { resize: vertical; }
  .form-actions { display: flex; gap: 6px; }
  .save-btn, .cancel-btn {
    font-size: 11px; padding: 3px 12px; border-radius: 4px; cursor: pointer;
  }
  .save-btn { background: var(--accent); color: #fff; border: none; }
  .save-btn:disabled { opacity: 0.5; }
  .cancel-btn { background: none; border: 1px solid var(--border); color: var(--text); }
  .job-list { flex: 1; overflow-y: auto; }
  .job-item {
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
  }
  .job-item:hover { background: var(--surface2); }
  .job-header {
    display: flex;
    align-items: center;
    gap: 8px;
  }
  .job-toggle {
    display: flex;
    align-items: center;
    gap: 6px;
    flex: 1;
    cursor: pointer;
  }
  .job-toggle input { margin: 0; }
  .job-name { font-size: 13px; font-weight: 500; overflow: hidden; text-overflow: ellipsis; }
  .job-schedule { font-size: 11px; color: var(--accent); font-family: var(--mono, monospace); flex-shrink: 0; }
  .delete-btn {
    background: none; border: none; color: var(--text2); cursor: pointer;
    font-size: 12px; padding: 0 4px; opacity: 0.5;
  }
  .delete-btn:hover { opacity: 1; color: #ef4444; }
  .job-meta {
    display: flex; gap: 12px; font-size: 10px; color: var(--text2);
    margin-top: 2px; margin-left: 22px;
  }
  .job-result {
    font-size: 10px; color: var(--text2); margin-top: 2px; margin-left: 22px;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }
  .empty { padding: 24px; text-align: center; color: var(--text2); font-size: 13px; }
</style>
