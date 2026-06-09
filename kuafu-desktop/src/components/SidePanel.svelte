<script lang="ts">
  import AgentTreePanel from "./AgentTreePanel.svelte";
  import SkillManager from "./SkillManager.svelte";
  import CronPanel from "./CronPanel.svelte";
  import ApprovalPanel from "./ApprovalPanel.svelte";

  let {
    onClose = () => {},
  }: { onClose: () => void } = $props();

  type Tab = "tree" | "skills" | "cron" | "approval";
  let activeTab = $state<Tab>("approval");
</script>

<div class="side-panel">
  <div class="tab-bar">
    <button class="tab" class:active={activeTab === "approval"} onclick={() => (activeTab = "approval")} title="审批">🔐</button>
    <button class="tab" class:active={activeTab === "tree"} onclick={() => (activeTab = "tree")} title="Agent树">🌳</button>
    <button class="tab" class:active={activeTab === "skills"} onclick={() => (activeTab = "skills")} title="技能">🧩</button>
    <button class="tab" class:active={activeTab === "cron"} onclick={() => (activeTab = "cron")} title="定时">⏰</button>
    <button class="close-btn" onclick={onClose}>✕</button>
  </div>

  <div class="tab-content">
    {#if activeTab === "approval"}
      <ApprovalPanel />
    {:else if activeTab === "tree"}
      <AgentTreePanel />
    {:else if activeTab === "skills"}
      <SkillManager />
    {:else if activeTab === "cron"}
      <CronPanel />
    {/if}
  </div>
</div>

<style>
  .side-panel {
    width: 340px;
    background: var(--surface);
    border-left: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    flex-shrink: 0;
  }
  .tab-bar {
    display: flex;
    align-items: center;
    gap: 2px;
    padding: 4px 6px;
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  .tab {
    background: none;
    border: none;
    padding: 4px 8px;
    font-size: 14px;
    cursor: pointer;
    border-radius: 4px;
    opacity: 0.5;
    transition: opacity 0.15s;
  }
  .tab:hover { opacity: 0.8; background: var(--surface2); }
  .tab.active { opacity: 1; background: var(--surface2); }
  .close-btn {
    margin-left: auto;
    background: none;
    border: none;
    font-size: 12px;
    cursor: pointer;
    color: var(--text2);
    padding: 4px 6px;
  }
  .close-btn:hover { color: var(--text); }
  .tab-content {
    flex: 1;
    overflow: hidden;
  }
</style>
