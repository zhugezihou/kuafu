<script lang="ts">
  import { Marked } from "marked";
  import { markedHighlight } from "marked-highlight";
  import hljs from "highlight.js";

  let { content = "" }: { content?: string } = $props();

  let html = $state("");

  const marked = new Marked(
    markedHighlight({
      langPrefix: "hljs language-",
      highlight(code: string, lang: string) {
        if (lang && hljs.getLanguage(lang)) {
          return hljs.highlight(code, { language: lang }).value;
        }
        return hljs.highlightAuto(code).value;
      },
    })
  );

  // 自定义代码块渲染：行号 + 复制按钮
  const renderer = {
    code({ text, lang }: { text: string; lang?: string }) {
      const langAttr = lang ? ` data-lang="${lang}"` : "";
      const lines = text.split("\n");
      // 去掉末尾空行
      while (lines.length > 0 && lines[lines.length - 1] === "") lines.pop();
      if (lines.length === 0) return "<pre><code></code></pre>";

      const lineNums = lines
        .map((_, i) => `<span class="line-num">${i + 1}</span>`)
        .join("\n");
      const codeHtml = lines
        .map((l) => escapeHtml(l))
        .join("\n");

      return `<div class="code-block">
        <div class="code-header">
          <span class="code-lang">${lang || ""}</span>
          <button class="copy-btn" onclick="(function(btn){
            const code = btn.closest('.code-block').querySelector('code').textContent;
            navigator.clipboard.writeText(code).then(() => {
              btn.textContent = '✓ 已复制';
              setTimeout(() => { btn.textContent = '📋 复制'; }, 2000);
            });
          })(this)">📋 复制</button>
        </div>
        <div class="code-body">
          <div class="line-numbers">${lineNums}</div>
          <pre><code${langAttr}>${codeHtml}</code></pre>
        </div>
      </div>`;
    }
  };

  // 配置 marked 选项
  marked.setOptions({
    breaks: true,
    gfm: true,
  });

  // 使用自定义 renderer
  marked.use({ renderer });

  // HTML 转义工具
  function escapeHtml(text: string): string {
    return text
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  $effect(() => {
    if (content) {
      html = marked.parse(content) as string;
    }
  });
</script>

<div class="markdown-body">
  {@html html}
</div>

<style>
  :global(pre code.hljs) {
    display: block;
    overflow-x: auto;
    padding: 1em;
  }
  :global(code.hljs) {
    padding: 3px 5px;
  }
  /* highlight.js github-dark 主题 */
  :global(.hljs) {
    color: #c9d1d9;
    background: #0d1117;
  }
  :global(.hljs-doctag),
  :global(.hljs-keyword),
  :global(.hljs-meta .hljs-keyword),
  :global(.hljs-template-tag),
  :global(.hljs-template-variable),
  :global(.hljs-type),
  :global(.hljs-variable.language_) {
    color: #ff7b72;
  }
  :global(.hljs-title),
  :global(.hljs-title.class_),
  :global(.hljs-title.class_.inherited__),
  :global(.hljs-title.function_) {
    color: #d2a8ff;
  }
  :global(.hljs-attr),
  :global(.hljs-attribute),
  :global(.hljs-literal),
  :global(.hljs-meta),
  :global(.hljs-number),
  :global(.hljs-operator),
  :global(.hljs-selector-attr),
  :global(.hljs-selector-class),
  :global(.hljs-selector-id),
  :global(.hljs-variable) {
    color: #79c0ff;
  }
  :global(.hljs-regexp),
  :global(.hljs-string),
  :global(.hljs-meta .hljs-string) {
    color: #a5d6ff;
  }
  :global(.hljs-built_in),
  :global(.hljs-symbol) {
    color: #ffa657;
  }
  :global(.hljs-comment),
  :global(.hljs-code),
  :global(.hljs-formula) {
    color: #8b949e;
  }
  :global(.hljs-name),
  :global(.hljs-quote),
  :global(.hljs-selector-tag),
  :global(.hljs-selector-pseudo) {
    color: #7ee787;
  }
  :global(.hljs-subst) {
    color: #c9d1d9;
  }
  :global(.hljs-section) {
    color: #1f6feb;
    font-weight: 700;
  }
  :global(.hljs-bullet) {
    color: #f2cc60;
  }
  :global(.hljs-emphasis) {
    color: #c9d1d9;
    font-style: italic;
  }
  :global(.hljs-strong) {
    color: #c9d1d9;
    font-weight: 700;
  }
  :global(.hljs-addition) {
    color: #aff5b4;
    background: #033a16;
  }
  :global(.hljs-deletion) {
    color: #ffdcd7;
    background: #67060c;
  }

  .markdown-body {
    line-height: 1.6;
    word-break: break-word;
  }

  .markdown-body :global(p) {
    margin: 0 0 8px;
  }

  .markdown-body :global(p:last-child) {
    margin-bottom: 0;
  }

  .markdown-body :global(code:not(pre code)) {
    background: rgba(255, 255, 255, 0.08);
    padding: 2px 6px;
    border-radius: 4px;
    font-size: 0.9em;
  }

  .markdown-body :global(pre) {
    background: #0d1117;
    border-radius: 8px;
    overflow-x: auto;
    margin: 8px 0;
    position: relative;
  }

  .markdown-body :global(pre code) {
    background: none;
    padding: 0;
    font-size: 13px;
    line-height: 1.5;
  }

  .markdown-body :global(ul), .markdown-body :global(ol) {
    padding-left: 20px;
    margin: 6px 0;
  }

  .markdown-body :global(li) {
    margin: 2px 0;
  }

  .markdown-body :global(strong) {
    font-weight: 600;
  }

  .markdown-body :global(a) {
    color: var(--accent);
    text-decoration: none;
  }

  .markdown-body :global(a:hover) {
    text-decoration: underline;
  }

  .markdown-body :global(blockquote) {
    border-left: 3px solid var(--accent);
    margin: 8px 0;
    padding: 4px 12px;
    color: var(--text2);
  }

  .markdown-body :global(table) {
    border-collapse: collapse;
    width: 100%;
    margin: 8px 0;
  }

  .markdown-body :global(th), .markdown-body :global(td) {
    border: 1px solid var(--border);
    padding: 6px 10px;
    text-align: left;
    font-size: 13px;
  }

  .markdown-body :global(th) {
    background: var(--surface2);
  }

  .markdown-body :global(h1), .markdown-body :global(h2), .markdown-body :global(h3) {
    margin: 12px 0 6px;
  }

  .markdown-body :global(h1) { font-size: 1.3em; }
  .markdown-body :global(h2) { font-size: 1.15em; }
  .markdown-body :global(h3) { font-size: 1.05em; }

  .markdown-body :global(hr) {
    border: none;
    border-top: 1px solid var(--border);
    margin: 12px 0;
  }

  /* 行号 + 复制按钮样式 */
  .markdown-body :global(.code-block) {
    margin: 8px 0;
    border-radius: 8px;
    overflow: hidden;
    border: 1px solid var(--border);
  }
  .markdown-body :global(.code-header) {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 6px 12px;
    background: #161b22;
    border-bottom: 1px solid var(--border);
    font-size: 12px;
  }
  .markdown-body :global(.code-lang) {
    color: #8b949e;
    text-transform: lowercase;
  }
  .markdown-body :global(.copy-btn) {
    background: none;
    border: 1px solid var(--border);
    color: #8b949e;
    padding: 2px 8px;
    border-radius: 4px;
    cursor: pointer;
    font-size: 11px;
  }
  .markdown-body :global(.copy-btn:hover) {
    background: rgba(255, 255, 255, 0.05);
    color: #c9d1d9;
  }
  .markdown-body :global(.code-body) {
    display: flex;
    background: #0d1117;
  }
  .markdown-body :global(.line-numbers) {
    display: flex;
    flex-direction: column;
    padding: 1em 0;
    min-width: 40px;
    text-align: right;
    user-select: none;
    color: #484f58;
    border-right: 1px solid #21262d;
    font-size: 13px;
    line-height: 1.5;
    font-family: monospace;
  }
  .markdown-body :global(.line-num) {
    padding: 0 10px;
  }
  .markdown-body :global(.code-body pre) {
    flex: 1;
    margin: 0;
    border-radius: 0;
    border: none;
  }
  .markdown-body :global(.code-body pre code) {
    padding: 1em;
  }
</style>
