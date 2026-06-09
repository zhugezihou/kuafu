<script lang="ts">
  import { messages, editMessage, deleteMessage } from "../lib/store";
  import MarkdownRenderer from "./MarkdownRenderer.svelte";

  let msgContainer: HTMLDivElement | undefined = $state();
  let hoveredIdx = $state<number | null>(null);
  let copiedIdx = $state<number | null>(null);
  let editingIdx = $state<number | null>(null);
  let editText = $state("");
  let searchQuery = $state("");
  let showSearch = $state(false);

  $effect(() => {
    // 自动滚动到底部
    if (msgContainer && !showSearch) {
      const msgs = $messages;
      requestAnimationFrame(() => {
        msgContainer!.scrollTop = msgContainer!.scrollHeight;
      });
    }
  });

  // 搜索过滤
  let filteredMessages = $derived.by(() => {
    if (!searchQuery.trim()) return $messages;
    const q = searchQuery.toLowerCase();
    return $messages.filter((m) => m.content.toLowerCase().includes(q));
  });

  function formatTime(ts?: number): string {
    if (!ts) return "";
    const d = new Date(ts);
    const now = new Date();
    const diff = now.getTime() - d.getTime();
    if (diff < 60000) return "刚刚";
    const h = d.getHours().toString().padStart(2, "0");
    const m = d.getMinutes().toString().padStart(2, "0");
    if (diff < 86400000) return `${h}:${m}`;
    return `${d.getMonth() + 1}月${d.getDate()}日 ${h}:${m}`;
  }

  function copyContent(content: string, idx: number) {
    navigator.clipboard.writeText(content);
    copiedIdx = idx;
    setTimeout(() => { copiedIdx = null; }, 2000);
  }

  function startEdit(idx: number, content: string) {
    editingIdx = idx;
    editText = content;
  }

  function commitEdit(idx: number) {
    if (editText.trim()) {
      editMessage(idx, editText.trim());
    }
    editingIdx = null;
  }

  function handleEditKeydown(e: KeyboardEvent, idx: number) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      commitEdit(idx);
    }
    if (e.key === "Escape") {
      editingIdx = null;
    }
  }

  function handleDelete(idx: number) {
    deleteMessage(idx);
  }
</script>

<div class="list" bind:this={msgContainer}>
  {#if $messages.length === 0}
    <div class="empty">
      <div class="empty-icon">夸</div>
      <div class="empty-text">夸父 Desktop</div>
      <div class="empty-hint">在下方输入开始对话</div>
    </div>
  {/if}

  <div class="search-bar" class:active={showSearch}>
    <input
      type="text"
      placeholder="搜索对话…"
      bind:value={searchQuery}
      onfocus={() => (showSearch = true)}
      onblur={() => { if (!searchQuery) showSearch = false; }}
    />
    {#if searchQuery}
      <span class="search-count">{filteredMessages.length} / {$messages.length}</span>
      <button class="search-close" onclick={() => { searchQuery = ""; showSearch = false; }}>✕</button>
    {/if}
  </div>

  {#each filteredMessages as msg, i (i)}
    <div class="message" class:user={msg.role === "user"} class:assistant={msg.role === "assistant"}
         onmouseenter={() => (hoveredIdx = i)}
         onmouseleave={() => (hoveredIdx = null)}>
      <div class="avatar">
        {msg.role === "user" ? "你" : "夸"}
      </div>
      <div class="content">
        <div class="meta-row">
          <span class="role-label">{msg.role === "user" ? "你" : "夸父"}</span>
          <span class="timestamp">{formatTime(msg.timestamp)}</span>
          {#if hoveredIdx === i}
            <button class="copy-btn" onclick={() => copyContent(msg.content, i)}>
              {copiedIdx === i ? "✓" : "📋"}
            </button>
            <button class="edit-btn" onclick={() => startEdit(i, msg.content)}>✎</button>
            <button class="delete-btn" onclick={() => handleDelete(i)}>🗑</button>
          {/if}
        </div>
        {#if editingIdx === i}
          <div class="edit-area">
            <textarea bind:value={editText} onkeydown={(e) => handleEditKeydown(e, i)} rows="3"></textarea>
            <div class="edit-actions">
              <button class="save-btn" onclick={() => commitEdit(i)}>保存</button>
              <button class="cancel-btn" onclick={() => (editingIdx = null)}>取消</button>
            </div>
          </div>
        {:else if msg.role === "user"}
          <div class="text">{msg.content}{#if msg.edited} <span class="edited-badge">已编辑</span>{/if}</div>
        {:else}
          <MarkdownRenderer content={msg.content} />
          {#if msg.edited}<div class="edited-badge">已编辑</div>{/if}
        {/if}
      </div>
    </div>
  {/each}
</div>

<style>
  .list {
    flex: 1;
    overflow-y: auto;
    padding: 0 16px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .search-bar {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 0;
    border-bottom: 1px solid var(--border);
    position: sticky;
    top: 0;
    background: var(--bg);
    z-index: 10;
  }
  .search-bar input {
    flex: 1;
    padding: 4px 8px;
    font-size: 12px;
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 4px;
    color: var(--text);
    outline: none;
  }
  .search-bar.active input { border-color: var(--accent); }
  .search-count { font-size: 11px; color: var(--text2); }
  .search-close { background: none; border: none; color: var(--text2); cursor: pointer; font-size: 12px; }

  .empty {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 12px;
    color: var(--text2);
  }

  .empty-icon {
    width: 48px;
    height: 48px;
    background: var(--accent);
    border-radius: 12px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 24px;
    font-weight: 700;
    color: #fff;
  }

  .empty-text {
    font-size: 18px;
    font-weight: 600;
    color: var(--text);
  }

  .empty-hint {
    font-size: 13px;
  }

  .message {
    display: flex;
    gap: 12px;
    padding: 12px 16px;
    border-radius: var(--radius);
    max-width: 85%;
  }

  .message.user {
    align-self: flex-end;
    background: var(--accent);
    color: #fff;
    flex-direction: row-reverse;
  }

  .message.assistant {
    align-self: flex-start;
    background: var(--surface);
  }

  .avatar {
    width: 28px;
    height: 28px;
    border-radius: 50%;
    background: var(--surface2);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
    font-weight: 600;
    flex-shrink: 0;
  }

  .message.user .avatar {
    background: rgba(255, 255, 255, 0.2);
  }

  .content {
    min-width: 0;
  }

  .role-label {
    font-size: 11px;
    font-weight: 600;
    opacity: 0.7;
  }

  .meta-row {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 4px;
  }

  .timestamp {
    font-size: 10px;
    color: var(--text2);
    opacity: 0.5;
  }

  .copy-btn {
    background: none;
    border: 1px solid var(--border);
    color: var(--text2);
    padding: 0 6px;
    border-radius: 3px;
    cursor: pointer;
    font-size: 11px;
    line-height: 1.6;
    opacity: 0.6;
  }
  .copy-btn:hover {
    opacity: 1;
    background: rgba(255,255,255,0.05);
  }

  .text {
    white-space: pre-wrap;
    word-break: break-word;
    line-height: 1.5;
    font-size: 14px;
  }

  .edit-btn, .delete-btn {
    background: none;
    border: none;
    color: var(--text2);
    cursor: pointer;
    font-size: 12px;
    padding: 0 4px;
    opacity: 0.5;
    line-height: 1.6;
  }
  .edit-btn:hover, .delete-btn:hover { opacity: 1; }
  .delete-btn:hover { color: #ef4444; }

  .edit-area {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  .edit-area textarea {
    width: 100%;
    padding: 6px 8px;
    font-size: 13px;
    border: 1px solid var(--accent);
    border-radius: 6px;
    background: var(--bg);
    color: var(--text);
    resize: vertical;
  }
  .edit-actions { display: flex; gap: 6px; }
  .save-btn, .cancel-btn {
    font-size: 12px; padding: 3px 12px; border-radius: 4px; cursor: pointer;
  }
  .save-btn {
    background: var(--accent); color: #fff; border: none;
  }
  .cancel-btn {
    background: transparent; border: 1px solid var(--border); color: var(--text);
  }
  .edited-badge {
    font-size: 10px; color: var(--text2); opacity: 0.6; margin-left: 4px;
  }
  .message.assistant .edited-badge { margin-left: 0; margin-top: 4px; }
</style>
