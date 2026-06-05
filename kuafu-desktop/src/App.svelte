<script lang="ts">
  import Sidebar from "./components/Sidebar.svelte";
  import MessageList from "./components/MessageList.svelte";
  import MessageInput from "./components/MessageInput.svelte";
  import StatusBar from "./components/StatusBar.svelte";
  import {
    messages,
    sessions,
    currentSessionId,
    isRunning,
    agentStatus,
    clearMessages,
    addMessage,
  } from "./lib/store";
  import { sendMessage, getStatus } from "./lib/gateway";

  let sidebarOpen = $state(true);

  // 初始加载
  $effect(() => {
    getStatus().then((s) => agentStatus.set(s));
  });

  async function handleSend(text: string) {
    if (!text.trim()) return;

    isRunning.set(true);
    addMessage({ role: "user", content: text });

    try {
      const result = await sendMessage(text);
      addMessage({ role: "assistant", content: result || "(完成)" });
    } catch (e: any) {
      addMessage({ role: "assistant", content: `错误: ${e.message}` });
    } finally {
      isRunning.set(false);
    }
  }

  function handleNewChat() {
    clearMessages();
  }
</script>

<div class="app">
  {#if sidebarOpen}
    <Sidebar
      onClose={() => (sidebarOpen = false)}
      onNewChat={handleNewChat}
    />
  {/if}

  <div class="main">
    <header class="header">
      <button class="menu-btn" onclick={() => (sidebarOpen = !sidebarOpen)}>
        ☰
      </button>
      <div class="header-title">夸父 Desktop</div>
      <button class="new-btn" onclick={handleNewChat}>＋ 新对话</button>
    </header>

    <div class="chat-area">
      <MessageList />
    </div>

    <MessageInput onSend={handleSend} disabled={$isRunning} />
    <StatusBar />
  </div>
</div>

<style>
  .app {
    display: flex;
    height: 100dvh;
  }

  .main {
    flex: 1;
    display: flex;
    flex-direction: column;
    min-width: 0;
  }

  .header {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 0 14px;
    height: var(--header-h);
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }

  .menu-btn {
    background: none;
    font-size: 18px;
    padding: 4px 8px;
  }

  .header-title {
    flex: 1;
    font-weight: 600;
    font-size: 15px;
  }

  .new-btn {
    font-size: 13px;
  }

  .chat-area {
    flex: 1;
    overflow-y: auto;
    padding: 12px 0;
  }
</style>
