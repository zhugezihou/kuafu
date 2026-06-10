<script lang="ts">
  import { onMount } from "svelte";
  import { agentRunning, agentError } from "../lib/store";
  import { loadConfig, saveConfig } from "../lib/config";

  let invokeFn: any = null;

  // 组件挂载时就自动初始化 invoke 并启动
  onMount(() => {
    import("@tauri-apps/api/core").then((core) => {
      invokeFn = core.invoke;
      // invoke 就绪后自动运行检测
      runSetup();
    }).catch(() => {});
  });

  interface SetupCheck {
    label: string;
    key: string;
    status: "pending" | "ok" | "fail" | "running";
    detail: string;
  }

  let checks = $state<SetupCheck[]>([
    { label: "Python 环境", key: "python", status: "pending", detail: "" },
    { label: "PyYAML 依赖", key: "pyyaml", status: "pending", detail: "" },
    { label: "夸父核心模块", key: "kuafu", status: "pending", detail: "" },
    { label: "引擎启动", key: "gateway", status: "pending", detail: "" },
  ]);
  let overallStatus = $state<"pending" | "running" | "ok" | "fail">("pending");
  let logLines = $state<string[]>([]);
  let showLog = $state(false);
  let timeoutSec = $state(0);
  let { onComplete }: { onComplete?: (ok: boolean) => void } = $props();

  // API Key 配置状态
  let showApiKeyForm = $state(false);
  let apiKey = $state("");
  let apiProvider = $state<"deepseek" | "openai" | "custom">("deepseek");
  let apiBaseUrl = $state("https://api.deepseek.com");
  let apiModel = $state("deepseek-chat");
  let savingKey = $state(false);

  function addLog(msg: string) {
    logLines = [...logLines, `[${new Date().toLocaleTimeString()}] ${msg}`];
  }

  async function handleSaveApiKey() {
    if (!apiKey.trim()) return;
    savingKey = true;
    try {
      const cfg = loadConfig();
      const baseUrl = apiProvider === "deepseek" ? "https://api.deepseek.com"
        : apiProvider === "openai" ? "https://api.openai.com/v1"
        : apiBaseUrl;
      const modelName = apiModel || (apiProvider === "deepseek" ? "deepseek-chat" : "gpt-4o");
      saveConfig({
        ...cfg,
        cloudProvider: apiProvider,
        cloudApiKey: apiKey.trim(),
        cloudBaseUrl: baseUrl,
        cloudModel: modelName,
        setupComplete: true,
      });
      addLog("✓ API Key 已保存");
      savingKey = false;
      if (onComplete) onComplete(true);
    } catch (e: any) {
      addLog(`✗ 保存失败: ${e.message || e}`);
      savingKey = false;
    }
  }

  function updateCheck(key: string, status: SetupCheck["status"], detail: string) {
    checks = checks.map(c => c.key === key ? { ...c, status, detail } : c);
  }

  export async function runSetup(): Promise<boolean> {
    if (!invokeFn) {
      addLog("✗ Tauri API 未就绪");
      overallStatus = "fail";
      return false;
    }

    overallStatus = "running";
    addLog("开始环境检测...");

    // 先设置配置
    try {
      const config = loadConfig();
      await invokeFn("update_agent_config", {
        config: {
          cloud_api_key: config.cloudApiKey,
          cloud_base_url: config.cloudBaseUrl,
          cloud_provider: config.cloudProvider,
          cloud_model: config.cloudModel,
        },
      });
      addLog("✓ 配置已加载");
    } catch (e: any) {
      addLog(`⚠ 配置加载跳过: ${e.message || e}`);
    }

    // 1. 检测 Python
    updateCheck("python", "running", "检测中...");
    addLog("检测 Python 环境...");
    try {
      const setup = await invokeFn("check_setup") as any;
      if (setup.python_found) {
        updateCheck("python", "ok", `路径: ${setup.python_path}`);
        addLog(`✓ Python 已找到: ${setup.python_path}`);
      } else {
        updateCheck("python", "fail", "未找到 Python，请安装 Python 3.11+");
        addLog("✗ Python 未找到");
        overallStatus = "fail";
        return false;
      }

      // 2. 检测 PyYAML
      if (setup.pyyaml_installed) {
        updateCheck("pyyaml", "ok", "已安装");
        addLog("✓ PyYAML 已安装");
      } else {
        updateCheck("pyyaml", "running", "正在安装...");
        addLog("PyYAML 未安装，尝试自动安装...");
        try {
          const autoResult = await invokeFn("auto_setup") as any;
          // auto_setup 返回 Ok(SetupStatus) 或 Err(String)
          if (autoResult && (autoResult.pyyaml_installed || autoResult.setup_complete)) {
            updateCheck("pyyaml", "ok", "安装成功");
            addLog("✓ PyYAML 安装成功");
          } else {
            const errMsg = autoResult?.error || "自动安装失败";
            updateCheck("pyyaml", "fail", errMsg);
            addLog(`✗ PyYAML 安装失败: ${errMsg}`);
            overallStatus = "fail";
            return false;
          }
        } catch (e: any) {
          updateCheck("pyyaml", "fail", `安装异常: ${e.message || e}`);
          addLog(`✗ PyYAML 安装异常: ${e.message || e}`);
          overallStatus = "fail";
          return false;
        }
      }

      // 3. 检测夸父模块
      if (setup.kuafu_found) {
        updateCheck("kuafu", "ok", "模块完整");
        addLog("✓ 夸父核心模块存在");
      } else {
        updateCheck("kuafu", "fail", "夸父模块缺失，安装包可能不完整");
        addLog("✗ 夸父模块缺失");
        overallStatus = "fail";
        return false;
      }

      // 4. 启动引擎（单独 try-catch，和前面的检测分开）
      updateCheck("gateway", "running", "启动中（最多等 10 秒）...");
      addLog("启动引擎...");
      try {
        const status = await invokeFn("start_agent") as any;
        if (status && status.running) {
          updateCheck("gateway", "ok", status.error || `端口 ${status.gateway_port}`);
          addLog(`✓ 引擎运行中 (PID: ${status.pid})`);
        } else {
          const errMsg = status?.error || "启动返回失败状态";
          updateCheck("gateway", "fail", errMsg);
          addLog(`✗ 引擎启动失败: ${errMsg}`);
          // 再查一次 agent_status 获取更详细的错误
          try {
            const st = await invokeFn("agent_status") as any;
            if (st && st.error) addLog(`  agent_status: ${st.error}`);
          } catch {}
          overallStatus = "fail";
          return false;
        }
      } catch (e: any) {
        const errMsg = e.message || String(e);
        updateCheck("gateway", "fail", errMsg);
        addLog(`✗ 引擎启动异常: ${errMsg}`);
        // 尝试获取更详细的错误
        try {
          const st = await invokeFn("agent_status") as any;
          if (st && st.error) addLog(`  agent_status: ${st.error}`);
        } catch {}
        overallStatus = "fail";
        return false;
      }

      overallStatus = "ok";
      addLog("✓ 环境检测通过！");

      // 检查是否已配置 API Key，未配置则弹出输入表单
      const currentCfg = loadConfig();
      if (currentCfg.cloudApiKey) {
        addLog("✓ API Key 已配置");
        if (onComplete) onComplete(true);
      } else {
        addLog("ℹ 需要配置 API Key");
        showApiKeyForm = true;
      }
      return true;
    } catch (e: any) {
      const msg = e.message || String(e);
      // 检查是哪个步骤抛的
      const runningCheck = checks.find(c => c.status === "running");
      if (runningCheck) {
        updateCheck(runningCheck.key, "fail", msg);
      }
      addLog(`✗ 异常: ${msg}`);
      overallStatus = "fail";
      return false;
    }
  }

  // 超时检测：如果 60 秒还没完成，显示提示
  let timer: ReturnType<typeof setInterval> | undefined;
  onMount(() => {
    timer = setInterval(() => {
      if (overallStatus === "running") {
        timeoutSec = timeoutSec + 1;
      }
    }, 1000);
    return () => { if (timer) clearInterval(timer); };
  });
</script>

<div class="setup-panel">
  <div class="setup-header">
    <h2>环境检测</h2>
    {#if overallStatus === "running"}
      <span class="badge running">检测中...</span>
    {:else if overallStatus === "ok"}
      <span class="badge ok">全部通过 ✓</span>
    {:else if overallStatus === "fail"}
      <span class="badge fail">检测失败</span>
    {:else}
      <span class="badge">等待启动</span>
    {/if}
  </div>

  <div class="check-list">
    {#each checks as check}
      <div class="check-item" class:active={check.status === "running"} class:fail={check.status === "fail"} class:ok={check.status === "ok"}>
        <div class="check-icon">
          {#if check.status === "ok"}
            <span class="icon-ok">✓</span>
          {:else if check.status === "fail"}
            <span class="icon-fail">✗</span>
          {:else if check.status === "running"}
            <span class="icon-running">⋯</span>
          {:else}
            <span class="icon-pending">○</span>
          {/if}
        </div>
        <div class="check-info">
          <div class="check-label">{check.label}</div>
          {#if check.detail}
            <div class="check-detail">{check.detail}</div>
          {/if}
        </div>
      </div>
    {/each}
  </div>

  {#if overallStatus === "running" && timeoutSec > 5}
    <div class="timeout-hint">
      {#if timeoutSec > 30}
        <p class="timeout-warn">⏱ 检测超过 {timeoutSec} 秒，可能卡住了，请点"查看详情"看日志</p>
      {:else}
        <p class="timeout-info">⏱ 检测进行中 ({timeoutSec}s)...</p>
      {/if}
    </div>
  {/if}

  {#if overallStatus === "fail"}
    <div class="fail-actions">
      <p class="fail-hint">环境检测未通过。</p>
      <button class="btn btn-retry" onclick={() => runSetup()}>⟳ 重试检测</button>
    </div>
  {/if}

  <button class="btn btn-log" onclick={() => showLog = !showLog}>
    {showLog ? "▼ 隐藏日志" : "▶ 查看详情"}
  </button>

  {#if showLog}
    <div class="log-area">
      {#each logLines as line}
        <div class="log-line">{line}</div>
      {/each}
    </div>
  {/if}

  {#if showApiKeyForm}
    <div class="apikey-section">
      <h3>🔑 配置大模型 API Key</h3>
      <p class="apikey-desc">夸父使用 DeepSeek 云端 API 进行对话。填入你的 API Key 即可开始使用。</p>

      <div class="field">
        <label>提供商</label>
        <select bind:value={apiProvider} class="apikey-select">
          <option value="deepseek">DeepSeek</option>
          <option value="openai">OpenAI</option>
          <option value="custom">自定义兼容</option>
        </select>
      </div>

      {#if apiProvider === "custom"}
        <div class="field">
          <label>API 地址</label>
          <input type="text" bind:value={apiBaseUrl} placeholder="https://api.xxx.com/v1" class="apikey-input" />
        </div>
      {:else if apiProvider === "openai"}
        <div class="field">
          <label>API 地址</label>
          <input type="text" bind:value={apiBaseUrl} placeholder="https://api.openai.com/v1" class="apikey-input" />
        </div>
      {/if}

      <div class="field">
        <label>API Key</label>
        <input type="password" bind:value={apiKey} placeholder={apiProvider === "deepseek" ? "sk-..." : "输入你的 API Key"} class="apikey-input" />
      </div>

      <div class="field">
        <label>模型名称</label>
        <input type="text" bind:value={apiModel} placeholder={apiProvider === "deepseek" ? "deepseek-chat" : "gpt-4o"} class="apikey-input" />
      </div>

      <div class="apikey-actions">
        <button class="btn btn-primary" onclick={handleSaveApiKey} disabled={savingKey || !apiKey.trim()}>
          {savingKey ? "保存中..." : "✅ 保存并继续"}
        </button>
        <button class="btn btn-skip" onclick={() => { if (onComplete) onComplete(false); }}>
          跳过（稍后设置）
        </button>
      </div>
    </div>
  {/if}
</div>

<style>
  .setup-panel {
    background: var(--surface, #1a1a2e);
    border: 1px solid var(--border, #2a2a4a);
    border-radius: 12px;
    padding: 20px;
    margin: 16px;
    max-width: 520px;
    width: calc(100% - 32px);
  }
  .setup-header {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 16px;
  }
  .setup-header h2 {
    margin: 0;
    font-size: 16px;
    font-weight: 600;
    flex: 1;
  }
  .badge {
    font-size: 11px;
    padding: 2px 8px;
    border-radius: 10px;
    background: var(--border, #2a2a4a);
    color: #888;
  }
  .badge.running { background: #1a4a6e; color: #6af; animation: pulse 1s infinite; }
  .badge.ok { background: #1a3a2a; color: #4caf50; }
  .badge.fail { background: #3a1a1a; color: #ef4444; }

  .check-list { display: flex; flex-direction: column; gap: 8px; margin-bottom: 16px; }
  .check-item {
    display: flex; align-items: center; gap: 10px;
    padding: 10px 12px;
    border-radius: 8px;
    background: rgba(255,255,255,0.03);
    border: 1px solid transparent;
    transition: all 0.2s;
  }
  .check-item.active { border-color: #4a6a8a; background: rgba(70, 130, 200, 0.08); }
  .check-item.fail { border-color: #6a2a2a; background: rgba(239, 68, 68, 0.08); }
  .check-item.ok { border-color: #2a5a2a; background: rgba(76, 175, 80, 0.06); }

  .check-icon { width: 22px; text-align: center; font-size: 16px; flex-shrink: 0; }
  .icon-ok { color: #4caf50; }
  .icon-fail { color: #ef4444; }
  .icon-running { color: #6af; animation: pulse 1s infinite; }
  .icon-pending { color: #555; }
  @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }

  .check-info { flex: 1; min-width: 0; }
  .check-label { font-size: 13px; font-weight: 500; }
  .check-detail { font-size: 11px; color: #888; margin-top: 2px; word-break: break-all; }

  .timeout-hint { margin-bottom: 12px; }
  .timeout-info { font-size: 12px; color: #6af; margin: 0; }
  .timeout-warn { font-size: 12px; color: #f59e0b; margin: 0; }

  .fail-actions { margin-bottom: 12px; }
  .fail-hint { font-size: 13px; color: #ef4444; margin: 0 0 8px 0; }

  .btn {
    padding: 8px 16px; border-radius: 6px; border: 1px solid var(--border, #2a2a4a);
    background: rgba(255,255,255,0.05); color: #ccc; font-size: 13px; cursor: pointer;
    transition: all 0.15s;
  }
  .btn:hover { background: rgba(255,255,255,0.1); }
  .btn-retry { background: rgba(239,68,68,0.15); border-color: rgba(239,68,68,0.3); color: #ef4444; }
  .btn-retry:hover { background: rgba(239,68,68,0.25); }
  .btn-log { width: 100%; margin-top: 4px; }

  .log-area {
    margin-top: 8px;
    background: rgba(0,0,0,0.3);
    border-radius: 6px;
    padding: 10px;
    max-height: 200px;
    overflow-y: auto;
    font-family: monospace;
    font-size: 11px;
  }
  .log-line { color: #888; line-height: 1.6; }

  .apikey-section {
    margin-top: 16px;
    border-top: 1px solid var(--border, #2a2a4a);
    padding-top: 16px;
  }
  .apikey-section h3 { margin: 0 0 8px 0; font-size: 15px; }
  .apikey-desc { font-size: 12px; color: #888; margin: 0 0 16px 0; line-height: 1.5; }
  .field { margin-bottom: 12px; }
  .field label { display: block; font-size: 12px; color: #aaa; margin-bottom: 4px; }
  .apikey-input, .apikey-select {
    width: 100%; padding: 8px 10px; border-radius: 6px; border: 1px solid var(--border, #2a2a4a);
    background: rgba(0,0,0,0.2); color: #eee; font-size: 13px; box-sizing: border-box;
  }
  .apikey-select { cursor: pointer; }
  .apikey-actions { display: flex; gap: 8px; margin-top: 16px; }
  .btn-primary {
    flex: 1; padding: 10px 16px; border-radius: 6px; border: none;
    background: #2563eb; color: #fff; font-size: 14px; font-weight: 500; cursor: pointer;
  }
  .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
  .btn-primary:hover:not(:disabled) { background: #1d4ed8; }
  .btn-skip {
    padding: 10px 16px; border-radius: 6px; border: 1px solid var(--border, #2a2a4a);
    background: transparent; color: #888; font-size: 13px; cursor: pointer;
  }
  .btn-skip:hover { background: rgba(255,255,255,0.05); }
</style>
