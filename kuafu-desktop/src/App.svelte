<script lang="ts">
  import { onMount } from "svelte";
  import Sidebar from "./components/Sidebar.svelte";
  import MessageList from "./components/MessageList.svelte";
  import MessageInput from "./components/MessageInput.svelte";
  import StatusBar from "./components/StatusBar.svelte";
  import Settings from "./components/Settings.svelte";
  import {
    messages,
    sessions,
    currentSessionId,
    isRunning,
    agentRunning,
    agentError,
    clearMessages,
    addMessage,
    appendToLastAssistant,
    loadSession,
    saveSession,
  } from "./lib/store";
  import { loadConfig } from "./lib/config";
  import { sendMessageStream } from "./lib/gateway";

  let sidebarOpen = $state(true);
  let showSettings = $state(false);
  let initialLoading = $state(true);
  let healthCheckInterval: ReturnType<typeof setInterval> | undefined;

  // Tauri invoke 引用（同步 import，避免运行时动态 import 出错）
  let tauriCore: any = null;

  // 组件挂载后初始化
  onMount(async () => {
    // 同步 import Tauri API
    try {
      tauriCore = await import("@tauri-apps/api/core");
    } catch {}

    // 恢复上次会话
    loadSession();

    // 启动引擎
    await startAgent();
    initialLoading = false;

    // 启动健康检查
    healthCheckInterval = setInterval(checkHealth, 15000);

    // 窗口关闭时停止引擎
    window.addEventListener("beforeunload", handleBeforeUnload);

    return () => {
      if (healthCheckInterval) clearInterval(healthCheckInterval);
      window.removeEventListener("beforeunload", handleBeforeUnload);
    };
  });

  function handleBeforeUnload() {
    // 同步调用，beforeunload 是同步事件
    try {
      if (tauriCore) {
        tauriCore.invoke("stop_agent");
      }
    } catch {}
  }

  async function startAgent() {
    if (!tauriCore) {
      agentError.set("Tauri API 不可用");
      return;
    }

    try {
      // 先传配置
      const config = loadConfig();
      await tauriCore.invoke("update_agent_config", {
        config: {
          model_type: config.modelType,
          local_model_path: config.localModelPath,
          local_llm_endpoint: config.localLlmEndpoint,
          cloud_api_key: config.cloudApiKey,
          cloud_model: config.cloudModel,
        },
      });

      // 启动引擎
      const status = await tauriCore.invoke("start_agent") as any;
      agentRunning.set(status.running);
      if (status.error) agentError.set(status.error);
    } catch (e: any) {
      agentError.set(`启动引擎失败: ${e}`);
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
      agentRunning.set(false);
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
        () => {
          isRunning.set(false);
          saveSession();
        }
      );
    } catch (e: any) {
      appendToLastAssistant(`\n\n错误: ${e.message}`);
      isRunning.set(false);
    }
  }

  function handleNewChat() {
    clearMessages();
  }

  function handleOpenSettings() {
    showSettings = true;
  }

  function handleCloseSettings() {
    showSettings = false;
  }
</script>

<div class="app">
  {#if sidebarOpen}
    <Sidebar
      onClose={() => (sidebarOpen = false)}
      onNewChat={handleNewChat}
      onOpenSettings={handleOpenSettings}
    />
  {/if}

  <div class="main">
    <header class="header">
      <button class="menu-btn" onclick={() => (sidebarOpen = !sidebarOpen)}>
        ☰
      </button>
      <div class="header-title">夸父 Desktop</div>
      <button class="settings-btn" onclick={handleOpenSettings}>⚙</button>
      <button class="new-btn" onclick={handleNewChat}>＋ 新对话</button>
    </header>

    <div class="chat-area">
      {#if initialLoading}
        <div class="loading">正在启动夸父引擎...</div>
      {:else if $agentError}
        <div class="error-banner">{$agentError}</div>
      {/if}
      <MessageList />
    </div>

    <MessageInput onSend={handleSend} disabled={$isRunning || !$agentRunning} />
    <StatusBar />
  </div>
</div>

{#if showSettings}
  <Settings onClose={handleCloseSettings} />
{/if}

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

  .settings-btn {
    background: none;
    font-size: 16px;
    padding: 4px 8px;
  }

  .new-btn {
    font-size: 13px;
  }

  .chat-area {
    flex: 1;
    overflow-y: auto;
    padding: 12px 0;
  }

  .loading {
    text-align: center;
    padding: 20px;
    color: var(--text2);
    font-size: 14px;
  }

  .error-banner {
    background: rgba(239, 68, 68, 0.1);
    border: 1px solid rgba(239, 68, 68, 0.3);
    color: #ef4444;
    margin: 8px 16px;
    padding: 10px 14px;
    border-radius: 8px;
    font-size: 13px;
  }
</style>
