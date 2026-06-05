<script lang="ts">
  import { archivedSessions, currentSessionId, loadArchivedSession, deleteArchivedSession, clearMessages } from "../lib/store";

  let {
    onClose = () => {},
    onNewChat = () => {},
    onOpenSettings = () => {},
  }: { onClose: () => void; onNewChat: () => void; onOpenSettings: () => void } = $props();

  let hoveredId = $state<string | null>(null);

  function selectSession(id: string) {
    loadArchivedSession(id);
  }

  function handleNewChat() {
    clearMessages();
    onNewChat();
  }

  function formatTime(ts: number): string {
    const d = new Date(ts);
    const now = new Date();
    const diff = now.getTime() - d.getTime();
    if (diff < 60000) return "刚刚";
    if (diff < 3600000) return `${Math.floor(diff / 60000)}分钟前`;
    if (diff < 86400000) return `${Math.floor(diff / 3600000)}小时前`;
    return `${d.getMonth() + 1}月${d.getDate()}日`;
  }
</script>

<aside class="sidebar">
  <div class="sidebar-header">
    <span class="logo">夸</span>
    <span class="title">夸父</span>
    <button class="close-btn" onclick={onClose}>✕</button>
  </div>

  <button class="new-chat-btn" onclick={handleNewChat}>＋ 新对话</button>

  <div class="session-list">
    <div class="section-title">历史对话 ({$archivedSessions.length})</div>
    {#if $archivedSessions.length === 0}
      <div class="empty-hint">暂无历史记录</div>
    {/if}
    {#each $archivedSessions as session (session.id)}
      <button
        class="session-item"
        class:active={$currentSessionId === session.id}
        onclick={() => selectSession(session.id)}
        onmouseenter={() => (hoveredId = session.id)}
        onmouseleave={() => (hoveredId = null)}
      >
        <span class="session-title">{session.title}</span>
        <span class="session-meta">
          {#if hoveredId === session.id}
            <span class="delete-btn" onclick={(e) => { e.stopPropagation(); deleteArchivedSession(session.id); }}>✕</span>
          {:else}
            <span class="session-time">{formatTime(session.updatedAt)}</span>
          {/if}
        </span>
      </button>
    {/each}
  </div>

  <div class="sidebar-footer">
    <button class="settings-btn" onclick={onOpenSettings}>⚙ 设置</button>
  </div>
</aside>

<style>
  .sidebar {
    width: var(--sidebar-w);
    background: var(--surface);
    border-right: 1px solid var(--border);
    display: flex;
    flex-direction: column;
    flex-shrink: 0;
  }

  .sidebar-header {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 14px;
    border-bottom: 1px solid var(--border);
  }

  .logo {
    width: 28px;
    height: 28px;
    background: var(--accent);
    border-radius: 6px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-weight: 700;
    font-size: 16px;
    color: #fff;
  }

  .title {
    flex: 1;
    font-weight: 600;
  }

  .close-btn {
    background: none;
    font-size: 14px;
    padding: 2px 6px;
  }

  .sidebar-footer {
    border-top: 1px solid var(--border);
    padding: 8px 12px;
  }

  .settings-btn {
    width: 100%;
    padding: 6px;
    font-size: 12px;
    text-align: center;
    background: none;
    border: 1px solid var(--border);
    border-radius: 6px;
    cursor: pointer;
  }

  .settings-btn:hover {
    background: var(--surface2);
  }

  .new-chat-btn {
    margin: 10px 12px;
    padding: 8px;
    width: calc(100% - 24px);
    font-size: 13px;
  }

  .session-list {
    flex: 1;
    overflow-y: auto;
    padding: 4px 0;
  }

  .section-title {
    padding: 8px 14px 4px;
    font-size: 11px;
    color: var(--text2);
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  .empty-hint {
    padding: 20px;
    text-align: center;
    font-size: 12px;
    color: var(--text2);
  }

  .session-item {
    display: flex;
    align-items: center;
    gap: 8px;
    width: calc(100% - 8px);
    margin: 2px 4px;
    padding: 8px 12px;
    font-size: 13px;
    text-align: left;
    background: none;
    border-radius: 6px;
    cursor: pointer;
  }

  .session-item:hover:not(.active) {
    background: var(--surface2);
  }

  .session-item.active {
    background: var(--accent);
    color: #fff;
  }

  .session-title {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
    text-align: left;
  }

  .session-meta {
    flex-shrink: 0;
    font-size: 11px;
  }

  .session-time {
    color: var(--text2);
  }

  .session-item.active .session-time {
    color: rgba(255, 255, 255, 0.6);
  }

  .delete-btn {
    color: #ef4444;
    font-size: 12px;
    padding: 2px 4px;
    border-radius: 3px;
  }

  .delete-btn:hover {
    background: rgba(239, 68, 68, 0.1);
  }
</style>
