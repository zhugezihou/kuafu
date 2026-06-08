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
  import { log } from "./lib/debug";
  import DebugPanel from "./components/DebugPanel.svelte";
  import { loadConfig, saveConfig } from "./lib/config";

  let sidebarOpen = $state(true);
  let showSettings = $state(false);
  let healthCheckInterval: ReturnType<typeof setInterval> | undefined;

  let invokeFn: any = null;
  let checking = $state(false);
  let showSetup = $state(false);
  let showCloudGuide = $state(false);

  onMount(() => {
    loadSession();
    log("info", "App mounted, loading Tauri API...");

    // 检查是否首次运行
    const cfg = loadConfig();
    if (!cfg.setupComplete) {
      log("info", "首次运行，显示环境检测");
      showSetup = true;
    } else if (!cfg.cloudApiKey) {
      log("info", "未配置云端 API，显示引导");
      showCloudGuide = true;
    }

    // 预存 invoke 引用
    import("@tauri-apps/api/core").then((core) => {
      invokeFn = core.invoke;
      log("info", "Tauri API ready");
    }).catch((e) => {
      log("error", `Tauri API import failed: ${e}`);
    });

    // 每15秒检查引擎状态
    healthCheckInterval = setInterval(checkHealth, 15000);

    return () => {
      if (healthCheckInterval) clearInterval(healthCheckInterval);
      if (invokeFn) invokeFn("stop_agent").catch(() => {});
    };
  });
 
   async function startAgentAsync() {
    if (!invokeFn) return;
    log("info", "startAgentAsync: configuring and starting engine...");
    try {
      const config = (await import("./lib/config")).loadConfig();
      log("debug", `startAgentAsync: config loaded modelType=${config.modelType}`);
      await invokeFn("update_agent_config", {
        config: {
          model_type: config.modelType,
          local_model_path: config.localModelPath,
          local_llm_endpoint: config.localLlmEndpoint,
          cloud_api_key: config.cloudApiKey,
          cloud_base_url: config.cloudBaseUrl,
          cloud_provider: config.cloudProvider,
          cloud_model: config.cloudModel,
        },
      });
      const status = await invokeFn("start_agent") as any;
      log("info", `startAgentAsync: running=${status.running} error=${status.error || "none"}`);
      agentRunning.set(status.running);
      if (status.error) agentError.set(status.error);
    } catch (e: any) {
      const msg = e.message || String(e);
      log("error", `startAgentAsync failed: ${msg}`);
      agentError.set(`引擎启动失败: ${msg}`);
    }
  }

  async function handleRetry() {
    checking = true;
    agentError.set(null);
    await startAgentAsync();
    checking = false;
  }

  async function checkHealth() {
    log("debug", "checkHealth: pinging Gateway...");
    try {
      const resp = await fetch("http://localhost:8081/api/status", {
        signal: AbortSignal.timeout(5000),
      });
      if (resp.ok) {
        agentRunning.set(true);
        agentError.set(null);
        log("info", "checkHealth: Gateway OK");
      } else {
        agentRunning.set(false);
        log("warn", `checkHealth: Gateway returned ${resp.status}`);
      }
    } catch (e: any) {
      log("warn", `checkHealth: Gateway unreachable: ${e.message || e}`);
      agentRunning.set(false);
      if (!invokeFn) return;
      try {
        const st = await invokeFn("agent_status") as any;
        if (st.error) {
          log("error", `checkHealth: agent_status error=${st.error}`);
          agentError.set(st.error);
          // 尝试自动重启
          log("info", "checkHealth: attempting auto-restart...");
          await startAgentAsync();
        }
      } catch (e2: any) {
        log("error", `checkHealth: agent_status failed: ${e2.message || e2}`);
      }
    }
  }

  async function handleSend(text: string) {
    if (!text.trim()) return;
    log("info", `handleSend: "${text.slice(0, 50)}..."`);

    isRunning.set(true);
    addMessage({ role: "user", content: text });
    addMessage({ role: "assistant", content: "" });

    // 发消息前同步配置到引擎（热更新）
    try {
      if (invokeFn) {
        const cfg = loadConfig();
        await invokeFn("update_agent_config", {
          config: {
            model_type: cfg.modelType,
            local_model_path: cfg.localModelPath,
            local_llm_endpoint: cfg.localLlmEndpoint,
            cloud_api_key: cfg.cloudApiKey,
            cloud_base_url: cfg.cloudBaseUrl,
            cloud_provider: cfg.cloudProvider,
            cloud_model: cfg.cloudModel,
          },
        });
      }
    } catch {}  // 静默失败，不影响对话

    try {
      await sendMessageStream(
        text,
        (chunk) => appendToLastAssistant(chunk),
        () => { isRunning.set(false); }
      );
    } catch (e: any) {
      appendToLastAssistant(`\n\n错误: ${e.message}`);
      isRunning.set(false);
    }
  }

  function handleNewChat() { clearMessages(); }

  // Ctrl+Shift+D 切换调试面板
  let debugPanel: any;
  function handleKeydown(e: KeyboardEvent) {
    if (e.ctrlKey && e.shiftKey && e.key === 'D') {
      e.preventDefault();
      if (debugPanel) debugPanel.toggle();
    }
  }
  onMount(() => {
    document.addEventListener('keydown', handleKeydown);
    window.addEventListener('debug-toggle', () => {
      if (debugPanel) debugPanel.toggle();
    });
    return () => {
      document.removeEventListener('keydown', handleKeydown);
      window.removeEventListener('debug-toggle', () => {});
    };
  });
</script>

<div class="app">
  {#if showSetup}
    <div class="setup-overlay">
      <SetupWizard onComplete={(ok) => {
        if (ok) {
          const cfg = loadConfig();
          saveConfig({ ...cfg, setupComplete: true });
          showSetup = false;
          log("info", "setup complete, starting engine...");
          startAgentAsync();
        }
      }} />
    </div>
  {:else}
  {#if showCloudGuide}
    <div class="cloud-guide-overlay">
      <div class="cloud-guide-card">
        <h2>☁️ 配置云端大模型</h2>
        <p>夸父需要 API Key 才能运行。请配置你的云端模型提供商。</p>
        <div class="cloud-guide-actions">
          <button class="btn-primary" onclick={() => { showCloudGuide = false; showSettings = true; }}>
            去设置
          </button>
          <button class="btn-secondary" onclick={() => { showCloudGuide = false; }}>
            稍后配置
          </button>
        </div>
      </div>
    </div>
  {/if}
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
</div>

{#if showSettings}
  <Settings onClose={() => (showSettings = false)} />
{/if}

<DebugPanel bind:this={debugPanel} />

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
  .cloud-guide-overlay {
    position: fixed; inset: 0; z-index: 100;
    display: flex; align-items: center; justify-content: center;
    background: rgba(0, 0, 0, 0.6);
    backdrop-filter: blur(4px);
  }
  .cloud-guide-card {
    background: var(--surface, #1a1a2e);
    border: 1px solid var(--border, #2a2a3e);
    border-radius: 12px; padding: 32px; max-width: 400px;
    text-align: center;
  }
  .cloud-guide-card h2 { margin: 0 0 12px; font-size: 18px; }
  .cloud-guide-card p { margin: 0 0 20px; font-size: 14px; color: var(--text-secondary, #888); }
  .cloud-guide-actions { display: flex; gap: 10px; justify-content: center; }
  .btn-primary {
    padding: 8px 20px; border-radius: 6px; font-size: 14px;
    background: var(--accent, #6c63ff); color: #fff; border: none; cursor: pointer;
  }
  .btn-secondary {
    padding: 8px 20px; border-radius: 6px; font-size: 14px;
    background: transparent; color: var(--text, #ccc); border: 1px solid var(--border, #333); cursor: pointer;
  }
</style>
