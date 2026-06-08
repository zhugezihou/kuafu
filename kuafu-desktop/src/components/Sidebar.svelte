<script lang="ts">
  import { archivedSessions, currentSessionId, loadArchivedSession, deleteArchivedSession, renameArchivedSession, clearMessages } from "../lib/store";

  let {
    onClose = () => {},
    onNewChat = () => {},
    onOpenSettings = () => {},
  }: { onClose: () => void; onNewChat: () => void; onOpenSettings: () => void } = $props();

  let hoveredId = $state<string | null>(null);
  let renamingId = $state<string | null>(null);
  let renameText = $state("");

  function selectSession(id: string) {
    loadArchivedSession(id);
  }

  function exportSession(id: string) {
    const archives = JSON.parse(localStorage.getItem("kuafu-desktop-archives") || "[]");
    const entry = archives.find((a: any) => a.id === id);
    if (!entry) return;
    // 构建 Markdown
    let md = `# ${entry.title}\n\n`;
    md += `导出时间: ${new Date().toLocaleString("zh-CN")}\n\n---\n\n`;
    for (const msg of entry.messages) {
      const role = msg.role === "user" ? "**你**" : "**夸父**";
      md += `${role}:\n${msg.content}\n\n`;
    }
    // 下载
    const blob = new Blob([md], { type: "text/markdown" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${entry.title.slice(0, 20)}.md`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function handleNewChat() {
    clearMessages();
    onNewChat();
  }

  function startRename(id: string, currentTitle: string) {
    renamingId = id;
    renameText = currentTitle;
  }

  function commitRename() {
    if (renamingId && renameText.trim()) {
      renameArchivedSession(renamingId, renameText.trim());
    }
    renamingId = null;
  }

  function handleRenameKeydown(e: KeyboardEvent) {
    if (e.key === "Enter") {
      e.preventDefault();
      commitRename();
    }
    if (e.key === "Escape") {
      renamingId = null;
    }
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
        {#if renamingId === session.id}
          <input
            class="rename-input"
            type="text"
            bind:value={renameText}
            onkeydown={handleRenameKeydown}
            onblur={commitRename}
            onclick={(e) => e.stopPropagation()}
            autofocus
          />
        {:else}
          <span class="session-title" ondblclick={(e) => { e.stopPropagation(); startRename(session.id, session.title); }}>{session.title}</span>
          <span class="session-meta">
            {#if hoveredId === session.id}
              <span class="rename-btn" onclick={(e) => { e.stopPropagation(); startRename(session.id, session.title); }} title="重命名">✎</span>
              <span class="export-btn" onclick={(e) => { e.stopPropagation(); exportSession(session.id); }} title="导出">↓</span>
              <span class="delete-btn" onclick={(e) => { e.stopPropagation(); deleteArchivedSession(session.id); }}>✕</span>
            {:else}
              <span class="session-time">{formatTime(session.updatedAt)}</span>
            {/if}
          </span>
        {/if}
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
    display: flex;
    gap: 4px;
  }

  .rename-btn {
    color: var(--accent);
    font-size: 14px;
    padding: 0 2px;
    cursor: pointer;
  }
  .rename-btn:hover { opacity: 0.8; }

  .export-btn {
    color: #22c55e;
    font-size: 12px;
    padding: 0 2px;
    cursor: pointer;
  }
  .export-btn:hover { opacity: 0.8; }

  .rename-input {
    flex: 1;
    padding: 2px 6px;
    font-size: 13px;
    background: var(--bg);
    border: 1px solid var(--accent);
    border-radius: 4px;
    color: var(--text);
    outline: none;
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
