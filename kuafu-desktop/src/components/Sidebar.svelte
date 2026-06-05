<script lang="ts">
  import { sessions, currentSessionId, clearMessages } from "../lib/store";
  import { getSessions } from "../lib/gateway";

  let {
    onClose = () => {},
    onNewChat = () => {},
  }: { onClose: () => void; onNewChat: () => void } = $props();

  $effect(() => {
    getSessions().then((s) => sessions.set(s));
  });

  function selectSession(id: string) {
    currentSessionId.set(id);
  }
</script>

<aside class="sidebar">
  <div class="sidebar-header">
    <span class="logo">夸</span>
    <span class="title">夸父</span>
    <button class="close-btn" onclick={onClose}>✕</button>
  </div>

  <button class="new-chat-btn" onclick={onNewChat}>＋ 新对话</button>

  <div class="session-list">
    <div class="section-title">最近会话</div>
    {#each $sessions as session (session.id)}
      <button
        class="session-item"
        class:active={$currentSessionId === session.id}
        onclick={() => selectSession(session.id)}
      >
        <span class="session-title">{session.title || "新对话"}</span>
        <span class="session-count">{session.message_count}</span>
      </button>
    {/each}
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
  }

  .session-item.active {
    background: var(--accent);
    color: #fff;
  }

  .session-item:hover:not(.active) {
    background: var(--surface2);
  }

  .session-title {
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .session-count {
    font-size: 11px;
    color: var(--text2);
    background: var(--surface2);
    padding: 1px 6px;
    border-radius: 10px;
  }

  .session-item.active .session-count {
    color: #fff;
    background: rgba(255, 255, 255, 0.2);
  }
</style>
