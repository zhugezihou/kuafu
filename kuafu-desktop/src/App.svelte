<script lang="ts">
  import { onMount } from "svelte";
  import Sidebar from "./components/Sidebar.svelte";
  import MessageList from "./components/MessageList.svelte";
  import MessageInput from "./components/MessageInput.svelte";
  import StatusBar from "./components/StatusBar.svelte";
  import SetupWizard from "./components/SetupWizard.svelte";
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

  let sidebarOpen = $state(true);
  let showSettings = $state(false);
  let healthCheckInterval: ReturnType<typeof setInterval> | undefined;

  let invokeFn: any = null;
  let checking = $state(false);
  let showSetup = $state(true);

  onMount(() => {
    loadSession();

    // 预存 invoke 引用
    import("@tauri-apps/api/core").then((core) => {
      invokeFn = core.invoke;
      // invoke 已就绪（SetupWizard 内部自启动，不需要这里触发）
    }).catch(() => {});

    // 每15秒检查引擎状态
    healthCheckInterval = setInterval(checkHealth, 15000);

    return () => {
      if (healthCheckInterval) clearInterval(healthCheckInterval);
      if (invokeFn) invokeFn("stop_agent").catch(() => {});
    };
  });
 
   async function startAgentAsync() {
    if (!invokeFn) return;
    try {
      const config = (await import("./lib/config")).loadConfig();
      await invokeFn("update_agent_config", {
        config: {
          model_type: config.modelType,
          local_model_path: config.localModelPath,
          local_llm_endpoint: config.localLlmEndpoint,
          cloud_api_key: config.cloudApiKey,
          cloud_model: config.cloudModel,
        },
      });
      const status = await invokeFn("start_agent") as any;
      agentRunning.set(status.running);
      if (status.error) agentError.set(status.error);
    } catch (e: any) {
      agentError.set(`引擎启动失败: ${e.message || e}`);
    }
  }

  async function handleRetry() {
    checking = true;
    agentError.set(null);
    await startAgentAsync();
    checking = false;
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
      agentRunning.set(false);
      if (!invokeFn) return;
      try {
        const st = await invokeFn("agent_status") as any;
        if (st.error) agentError.set(st.error);
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
  {#if showSetup}
    <div class="setup-overlay">
      <SetupWizard onComplete={(ok) => { if (ok) { showSetup = false; agentRunning.set(true); } }} />
    </div>
  {:else}
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
        <div class="error-banner">
          <span>{$agentError}</span>
          <button class="retry-btn" onclick={handleRetry} disabled={checking}>
            {checking ? "⋯" : "⟳ 重试"}
          </button>
        </div>
      {/if}
      <MessageList />
    </div>

    <MessageInput onSend={handleSend} disabled={$isRunning || !$agentRunning} />
    <StatusBar />
  </div>
  {/if}
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
    display: flex; align-items: center; gap: 10px;
    background: rgba(239, 68, 68, 0.1); border: 1px solid rgba(239, 68, 68, 0.3);
    color: #ef4444; margin: 8px 16px; padding: 10px 14px; border-radius: 8px; font-size: 13px;
  }
  .error-banner span { flex: 1; }
  .retry-btn {
    font-size: 12px; padding: 4px 12px; background: rgba(239, 68, 68, 0.15);
    border: 1px solid rgba(239, 68, 68, 0.3); border-radius: 4px; color: #ef4444;
    cursor: pointer; white-space: nowrap;
  }
  .retry-btn:disabled { opacity: 0.5; cursor: not-allowed; }
  .setup-overlay {
    display: flex; align-items: center; justify-content: center;
    width: 100%; height: 100dvh;
    background: var(--bg, #0d0d1a);
  }
</style>
