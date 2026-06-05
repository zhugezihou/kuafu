<script lang="ts">
  import { messages } from "../lib/store";

  let msgContainer: HTMLDivElement | undefined = $state();

  $effect(() => {
    // 自动滚动到底部
    if (msgContainer) {
      $effect(() => {
        $messages;
        requestAnimationFrame(() => {
          msgContainer!.scrollTop = msgContainer!.scrollHeight;
        });
      });
    }
  });
</script>

<div class="list" bind:this={msgContainer}>
  {#if $messages.length === 0}
    <div class="empty">
      <div class="empty-icon">夸</div>
      <div class="empty-text">夸父 Desktop</div>
      <div class="empty-hint">在下方输入开始对话</div>
    </div>
  {/if}

  {#each $messages as msg, i (i)}
    <div class="message" class:user={msg.role === "user"} class:assistant={msg.role === "assistant"}>
      <div class="avatar">
        {msg.role === "user" ? "你" : "夸"}
      </div>
      <div class="content">
        <div class="role-label">{msg.role === "user" ? "你" : "夸父"}</div>
        <div class="text">{msg.content}</div>
      </div>
    </div>
  {/each}
</div>

<style>
  .list {
    flex: 1;
    overflow-y: auto;
    padding: 0 16px;
    display: flex;
    flex-direction: column;
    gap: 8px;
  }

  .empty {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 12px;
    color: var(--text2);
  }

  .empty-icon {
    width: 48px;
    height: 48px;
    background: var(--accent);
    border-radius: 12px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 24px;
    font-weight: 700;
    color: #fff;
  }

  .empty-text {
    font-size: 18px;
    font-weight: 600;
    color: var(--text);
  }

  .empty-hint {
    font-size: 13px;
  }

  .message {
    display: flex;
    gap: 12px;
    padding: 12px 16px;
    border-radius: var(--radius);
    max-width: 85%;
  }

  .message.user {
    align-self: flex-end;
    background: var(--accent);
    color: #fff;
    flex-direction: row-reverse;
  }

  .message.assistant {
    align-self: flex-start;
    background: var(--surface);
  }

  .avatar {
    width: 28px;
    height: 28px;
    border-radius: 50%;
    background: var(--surface2);
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 12px;
    font-weight: 600;
    flex-shrink: 0;
  }

  .message.user .avatar {
    background: rgba(255, 255, 255, 0.2);
  }

  .content {
    min-width: 0;
  }

  .role-label {
    font-size: 11px;
    font-weight: 600;
    margin-bottom: 4px;
    opacity: 0.7;
  }

  .text {
    white-space: pre-wrap;
    word-break: break-word;
    line-height: 1.5;
    font-size: 14px;
  }
</style>
