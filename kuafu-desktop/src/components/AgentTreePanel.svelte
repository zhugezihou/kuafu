<script lang="ts">
  import { onMount, onDestroy } from "svelte";
  import { getAgentTree, type AgentTreeNode } from "../lib/gateway";
  import { log } from "../lib/debug";

  let tree = $state<AgentTreeNode | null>(null);
  let expanded = $state<Set<string>>(new Set(["/"]));
  let loading = $state(true);
  let autoRefresh = $state(true);
  let timer: ReturnType<typeof setInterval> | undefined;

  $effect(() => {
    // 只在 visible 时才启动定时刷新
    if (autoRefresh) {
      timer = setInterval(loadTree, 3000);
    } else {
      if (timer) clearInterval(timer);
    }
    return () => { if (timer) clearInterval(timer); };
  });

  async function loadTree() {
    try {
      tree = await getAgentTree();
    } catch (e: any) {
      log("warn", `AgentTreePanel: ${e.message || e}`);
    } finally {
      loading = false;
    }
  }

  function toggleNode(path: string) {
    if (expanded.has(path)) expanded.delete(path);
    else expanded.add(path);
    expanded = expanded; // 触发响应式
  }

  function formatDuration(sec?: number): string {
    if (!sec) return "";
    if (sec < 60) return `${sec.toFixed(1)}s`;
    return `${Math.floor(sec / 60)}m${(sec % 60).toFixed(0)}s`;
  }

  function renderNode(node: AgentTreeNode, depth = 0): string {
    const isExpanded = expanded.has(node.path);
    const hasChildren = node.children && node.children.length > 0;
    const indent = "  ".repeat(depth);
    const icon = node.status === "running" ? "●" : node.status === "done" ? "✓" : "○";
    const statusClass = node.status === "running" ? "running" : node.status === "done" ? "done" : "idle";
    return `${indent}<div class="tree-node ${statusClass}" style="padding-left: ${depth * 20}px">
      <button class="toggle-btn" onclick={() => toggleNode('${node.path}')}>
        ${hasChildren ? (isExpanded ? "▼" : "▶") : " "}
      </button>
      <span class="status-dot ${statusClass}">${icon}</span>
      <span class="node-name">${node.name}</span>
      ${node.tool_calls != null ? `<span class="node-meta">${node.tool_calls}次工具调用</span>` : ""}
      ${node.duration ? `<span class="node-meta">${formatDuration(node.duration)}</span>` : ""}
      ${node.token_usage ? `<span class="node-meta">${(node.token_usage.total / 1000).toFixed(1)}K tokens</span>` : ""}
    </div>`;
  }

  let htmlContent = $derived.by(() => {
    if (!tree) return "<div class='empty'>暂无 Agent 树数据</div>";
    return renderTree(tree, 0);
  });

  function renderTree(node: AgentTreeNode, depth: number): string {
    let html = renderNode(node, depth);
    if (expanded.has(node.path) && node.children) {
      for (const child of node.children) {
        html += renderTree(child, depth + 1);
      }
    }
    return html;
  }

  onMount(() => { loadTree(); });
</script>

<div class="agent-tree-panel">
  <div class="panel-header">
    <span class="panel-title">🌳 Agent 树</span>
    <div class="panel-actions">
      <label class="auto-refresh-label">
        <input type="checkbox" bind:checked={autoRefresh} /> 自动刷新
      </label>
      <button class="refresh-btn" onclick={loadTree} disabled={loading}>
        {loading ? "⋯" : "⟳"}
      </button>
    </div>
  </div>
  <div class="tree-content">
    {@html htmlContent}
  </div>
</div>

<style>
  .agent-tree-panel {
    display: flex;
    flex-direction: column;
    height: 100%;
  }
  .panel-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
    flex-shrink: 0;
  }
  .panel-title { font-weight: 600; font-size: 14px; }
  .panel-actions { display: flex; align-items: center; gap: 8px; }
  .auto-refresh-label { font-size: 11px; display: flex; align-items: center; gap: 4px; cursor: pointer; }
  .auto-refresh-label input { margin: 0; }
  .refresh-btn {
    background: none; border: 1px solid var(--border); border-radius: 4px;
    padding: 2px 8px; cursor: pointer; font-size: 12px;
  }
  .refresh-btn:disabled { opacity: 0.5; }
  .tree-content {
    flex: 1;
    overflow-y: auto;
    padding: 8px 0;
    font-family: var(--mono, monospace);
    font-size: 12px;
  }
  .tree-node {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 3px 8px;
    white-space: nowrap;
  }
  .tree-node:hover { background: var(--surface2); }
  .toggle-btn {
    background: none; border: none; cursor: pointer;
    font-size: 10px; padding: 0; width: 14px; text-align: center;
    color: var(--text2); flex-shrink: 0;
  }
  .status-dot { font-size: 10px; width: 10px; text-align: center; }
  .status-dot.running { color: #22c55e; }
  .status-dot.done { color: #6b7280; }
  .node-name { flex: 1; overflow: hidden; text-overflow: ellipsis; }
  .node-meta { font-size: 10px; color: var(--text2); margin-left: 6px; flex-shrink: 0; }
  .empty { padding: 24px; text-align: center; color: var(--text2); font-size: 13px; }
</style>
