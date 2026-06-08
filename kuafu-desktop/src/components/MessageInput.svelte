<script lang="ts">
  let {
    onSend = (_text: string) => {},
    disabled = false,
  }: { onSend: (text: string) => void; disabled: boolean } = $props();

  let text = $state("");

  function submit(e: Event) {
    e.preventDefault();
    if (!text.trim() || disabled) return;
    onSend(text);
    text = "";
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      // Ctrl+Enter 发送（改：防止误触，明确区分）
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        submit(e);
        return;
      }
      // 单独的 Enter 也发送（不改原有习惯）
      if (!e.ctrlKey && !e.shiftKey) {
        e.preventDefault();
        submit(e);
      }
    }
  }
</script>

<form class="input-area" onsubmit={submit}>
  <textarea
    bind:value={text}
    onkeydown={handleKeydown}
    placeholder="输入消息… （Enter 发送，Ctrl+Enter 换行）"
    rows="1"
    disabled={disabled}
  ></textarea>
  <button type="submit" class="primary send-btn" disabled={!text.trim() || disabled}>
    {disabled ? "⏳" : "↵"}
  </button>
</form>

<style>
  .input-area {
    display: flex;
    gap: 8px;
    padding: 10px 14px;
    border-top: 1px solid var(--border);
    background: var(--surface);
    flex-shrink: 0;
  }

  textarea {
    flex: 1;
    resize: none;
    max-height: 120px;
    line-height: 1.4;
  }

  .send-btn {
    width: 36px;
    height: 36px;
    padding: 0;
    font-size: 16px;
    display: flex;
    align-items: center;
    justify-content: center;
    flex-shrink: 0;
  }

  .send-btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }
</style>
