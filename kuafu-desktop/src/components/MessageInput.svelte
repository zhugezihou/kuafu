<script lang="ts">
  import { log } from "../lib/debug";

  let {
    onSend = (_text: string) => {},
    disabled = false,
  }: { onSend: (text: string) => void; disabled: boolean } = $props();

  let text = $state("");
  let isDragging = $state(false);

  function submit(e: Event) {
    e.preventDefault();
    if (!text.trim() || disabled) return;
    onSend(text);
    text = "";
  }

  function handleKeydown(e: KeyboardEvent) {
    if (e.key === "Enter" && !e.shiftKey) {
      if (e.ctrlKey || e.metaKey) {
        e.preventDefault();
        submit(e);
        return;
      }
      if (!e.ctrlKey && !e.shiftKey) {
        e.preventDefault();
        submit(e);
      }
    }
  }

  // 拖拽支持
  function handleDragOver(e: DragEvent) {
    e.preventDefault();
    isDragging = true;
  }
  function handleDragLeave() {
    isDragging = false;
  }
  function handleDrop(e: DragEvent) {
    e.preventDefault();
    isDragging = false;
    if (!e.dataTransfer?.files.length) return;
    const files = Array.from(e.dataTransfer.files);
    // 用文件名构建消息文本
    const fileInfo = files.map(f => `[文件] ${f.name} (${(f.size / 1024).toFixed(1)}KB)`).join("\n");
    if (text.trim()) {
      text = text + "\n" + fileInfo;
    } else {
      text = fileInfo;
    }
    log("info", `handleDrop: ${files.length} file(s)`);
  }
</script>

<form class="input-area" class:dragging={isDragging} onsubmit={submit}
      ondragover={handleDragOver} ondragleave={handleDragLeave} ondrop={handleDrop}>
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
    transition: border-color 0.15s;
  }
  .input-area.dragging {
    border-top: 2px dashed var(--accent);
    background: rgba(108, 99, 255, 0.05);
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
