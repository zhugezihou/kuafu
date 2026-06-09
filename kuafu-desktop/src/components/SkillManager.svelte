<script lang="ts">
  import { onMount } from "svelte";
  import { getSkills, toggleSkill, type SkillInfo } from "../lib/gateway";
  import { log } from "../lib/debug";

  let skills = $state<SkillInfo[]>([]);
  let loading = $state(true);
  let filter = $state("");

  let filteredSkills = $derived.by(() => {
    if (!filter.trim()) return skills;
    const q = filter.toLowerCase();
    return skills.filter(s => s.name.toLowerCase().includes(q) || (s.description || "").toLowerCase().includes(q));
  });

  onMount(async () => {
    skills = await getSkills();
    loading = false;
  });

  async function handleToggle(name: string, enabled: boolean) {
    const ok = await toggleSkill(name, enabled);
    if (ok) {
      skills = skills.map(s => s.name === name ? { ...s, enabled } : s);
      log("info", `toggleSkill: ${name} -> ${enabled}`);
    }
  }
</script>

<div class="skill-manager">
  <div class="panel-header">
    <span class="panel-title">🧩 技能管理器</span>
    <span class="skill-count">{skills.length} 个技能</span>
  </div>

  <div class="search-bar">
    <input type="text" placeholder="搜索技能…" bind:value={filter} />
  </div>

  <div class="skill-list">
    {#if loading}
      <div class="empty">加载中…</div>
    {:else if filteredSkills.length === 0}
      <div class="empty">无匹配的技能</div>
    {/if}
    {#each filteredSkills as skill (skill.name)}
      <div class="skill-item">
        <label class="skill-toggle">
          <input type="checkbox" checked={skill.enabled} onchange={(e) => handleToggle(skill.name, (e.target as HTMLInputElement).checked)} />
          <span class="skill-name">{skill.name}</span>
        </label>
        {#if skill.description}
          <div class="skill-desc">{skill.description}</div>
        {/if}
        {#if skill.category}
          <span class="skill-category">{skill.category}</span>
        {/if}
      </div>
    {/each}
  </div>
</div>

<style>
  .skill-manager {
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
  .skill-count { font-size: 11px; color: var(--text2); }
  .search-bar { padding: 6px 12px; }
  .search-bar input {
    width: 100%; padding: 4px 8px; font-size: 12px;
    background: var(--surface); border: 1px solid var(--border);
    border-radius: 4px; color: var(--text); outline: none;
    box-sizing: border-box;
  }
  .search-bar input:focus { border-color: var(--accent); }
  .skill-list {
    flex: 1;
    overflow-y: auto;
    padding: 4px 0;
  }
  .skill-item {
    padding: 8px 12px;
    border-bottom: 1px solid var(--border);
  }
  .skill-item:hover { background: var(--surface2); }
  .skill-toggle {
    display: flex;
    align-items: center;
    gap: 8px;
    cursor: pointer;
  }
  .skill-toggle input { margin: 0; }
  .skill-name { font-size: 13px; font-weight: 500; }
  .skill-desc {
    font-size: 11px;
    color: var(--text2);
    margin-top: 2px;
    margin-left: 22px;
  }
  .skill-category {
    display: inline-block;
    font-size: 10px; padding: 1px 6px; border-radius: 3px;
    background: var(--surface2); color: var(--text2);
    margin-top: 4px; margin-left: 22px;
  }
  .empty { padding: 24px; text-align: center; color: var(--text2); font-size: 13px; }
</style>
