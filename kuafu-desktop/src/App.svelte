<script lang="ts">
  import { onMount } from "svelte";
  import Sidebar from "./components/Sidebar.svelte";
  import MessageList from "./components/MessageList.svelte";
  import MessageInput from "./components/MessageInput.svelte";
  import StatusBar from "./components/StatusBar.svelte";
  import Settings from "./components/Settings.svelte";
  import {
    messages,
    isRunning,
    agentRunning,
    agentError,
    clearMessages,
    addMessage,
    appendToLastAssistant,
    loadSession,
    saveSession,
  } from "./lib/store";
  import { sendMessageStream } from "./lib/gateway";
  import { loadConfig } from "./lib/config";

  let sidebarOpen = $state(true);
  let showSettings = $state(false);
  let healthCheckInterval: ReturnType<typeof setInterval> | undefined;

  onMount(() => {
    loadSession();
    startAgentAsync();

    // 每15秒检查引擎状态
    healthCheckInterval = setInterval(checkHealth, 15000);

    return () => {
      if (healthCheckInterval) clearInterval(healthCheckInterval);
      // 窗口关闭时停止夸父引擎
      import("@tauri-apps/api/core").then(({ invoke }) => {
        invoke("stop_agent").catch(() => {});
      });
    };
  });

  async function startAgentAsync() {
    try {
      const { invoke } = await import("@tauri-apps/api/core");
      const config = loadConfig();
      await invoke("update_agent_config", {
        config: {
          model_type: config.modelType,
          local_model_path: config.localModelPath,
          local_llm_endpoint: config.localLlmEndpoint,
          cloud_api_key: config.cloudApiKey,
          cloud_model: config.cloudModel,
        },
      });
      const status = await invoke("start_agent") as any;
      agentRunning.set(status.running);
      if (status.error) agentError.set(status.error);
    } catch {
      // Tauri API 不可用时保持离线状态
    }
  }

  async function checkHealth() {
    try {
      const resp = await fetch("http://localhost:8081/api/status", {
        signal: AbortSignal.timeout(5000),
      });
      if (resp.ok) {
        agentRunning.set(true);
        agentError.set(null);
      } else {
        agentRunning.set(false);
      }
    } catch {
      // Gateway 不可达 — 读 Rust 端状态取错误信息
      agentRunning.set(false);
      try {
        const { invoke } = await import("@tauri-apps/api/core");
        const st = await invoke("agent_status") as any;
        if (st.error) agentError.set(st.error);
        // 尝试重新启动
        const result = await invoke("start_agent") as any;
        if (result.error) agentError.set(result.error);
      } catch {}
    }
  }

  async function handleSend(text: string) {
    if (!text.trim()) return;

    isRunning.set(true);
    addMessage({ role: "user", content: text });
    addMessage({ role: "assistant", content: "" });

    try {
      await sendMessageStream(
        text,
        (chunk) => appendToLastAssistant(chunk),
        () => { isRunning.set(false); saveSession(); }
      );
    } catch (e: any) {
      appendToLastAssistant(`\n\n错误: ${e.message}`);
      isRunning.set(false);
    }
  }

  function handleNewChat() { clearMessages(); }
</script>

<div class="app">
  {#if sidebarOpen}
    <Sidebar
      onClose={() => (sidebarOpen = false)}
      onNewChat={handleNewChat}
      onOpenSettings={() => (showSettings = true)}
    />
  {/if}

  <div class="main">
    <header class="header">
      <button class="menu-btn" onclick={() => (sidebarOpen = !sidebarOpen)}>☰</button>
      <div class="header-title">夸父 Desktop</div>
      <button class="settings-btn" onclick={() => (showSettings = true)}>⚙</button>
      <button class="new-btn" onclick={handleNewChat}>＋ 新对话</button>
    </header>

    <div class="chat-area">
      {#if $agentError}
        <div class="error-banner">{$agentError}</div>
      {/if}
      <MessageList />
    </div>

    <MessageInput onSend={handleSend} disabled={$isRunning || !$agentRunning} />
    <StatusBar />
  </div>
</div>

{#if showSettings}
  <Settings onClose={() => (showSettings = false)} />
{/if}

<style>
  .app { display: flex; height: 100dvh; }
  .main { flex: 1; display: flex; flex-direction: column; min-width: 0; }
  .header {
    display: flex; align-items: center; gap: 10px; padding: 0 14px;
    height: var(--header-h); background: var(--surface);
    border-bottom: 1px solid var(--border); flex-shrink: 0;
  }
  .menu-btn { background: none; font-size: 18px; padding: 4px 8px; }
  .header-title { flex: 1; font-weight: 600; font-size: 15px; }
  .settings-btn { background: none; font-size: 16px; padding: 4px 8px; }
  .new-btn { font-size: 13px; }
  .chat-area { flex: 1; overflow-y: auto; padding: 12px 0; }
  .error-banner {
    background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3);
    color: #ef4444; margin: 8px 16px; padding: 10px 14px; border-radius: 8px; font-size: 13px;
  }
</style>
