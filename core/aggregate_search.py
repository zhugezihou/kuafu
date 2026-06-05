"""
core/aggregate_search.py — 夸父高级聚合搜索

同时请求多个搜索引擎，去重合并，LLM 汇总。

设计：
- 并行请求 DDG + Bing + Tavily（线程池）
- URL 去重 + 智能合并
- LLM 汇总：综合各引擎结果生成结构化答案
- 零额外依赖，复用现有 web_search / tavily_search 函数

用法：
    from core.aggregate_search import aggregate_search
    result = aggregate_search(query, llm_chat_fn=llm.chat)
    # result["output"] 包含综合搜索结果
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional

logger = logging.getLogger("kuafu.aggregate_search")

# ── 搜索引擎实现（复用 tool_registry 逻辑，独立可运行）──


def search_duckduckgo(query: str, max_results: int = 5) -> list[dict]:
    """DuckDuckGo Lite 搜索。"""
    url = f"https://lite.duckduckgo.com/lite/?q={urllib.parse.quote(query)}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; KuafuAggregate/1.0)",
            "Accept": "text/html",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return []

    results = []
    # 主模式：DDG result-link
    link_pattern = re.compile(
        r'<a[^>]*class="result-link"[^>]*href="([^"]*)"[^>]*>([^<]*)</a>',
        re.IGNORECASE,
    )
    snippet_pattern = re.compile(
        r'<td[^>]*class="result-snippet"[^>]*>([^<]*)</td>',
        re.IGNORECASE,
    )
    links = link_pattern.findall(html)
    snippets = snippet_pattern.findall(html)

    for i, (href, title) in enumerate(links):
        if len(results) >= max_results:
            break
        snippet = snippets[i].strip() if i < len(snippets) else ""
        snippet = re.sub(r'<[^>]+>', ' ', snippet).strip()[:200]
        results.append({
            "title": title.strip() or "(无标题)",
            "url": href,
            "snippet": snippet,
            "source": "duckduckgo",
        })

    if not results:
        # fallback: 通用链接
        all_links = re.findall(
            r'<a[^>]*href="(https?://[^"]+)"[^>]*>([^<]*)</a>', html
        )
        seen = set()
        for href, title in all_links:
            if href not in seen and len(results) < max_results:
                seen.add(href)
                results.append({
                    "title": (title.strip() or href)[:100],
                    "url": href,
                    "snippet": "",
                    "source": "duckduckgo",
                })
    return results


def search_bing(query: str, max_results: int = 5) -> list[dict]:
    """Bing 搜索。"""
    url = f"https://www.bing.com/search?q={urllib.parse.quote(query)}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            html = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return []

    results = []
    # 主模式：Bing b_algo
    b_algo = re.findall(
        r'<li[^>]*class="[^"]*\bb_algo\b[^"]*"[^>]*>(.*?)</li>',
        html, re.DOTALL | re.IGNORECASE
    )
    for block in b_algo:
        if len(results) >= max_results:
            break
        link_m = re.search(
            r'<h2[^>]*>.*?<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>',
            block, re.DOTALL
        )
        if not link_m:
            continue
        url = link_m.group(1).strip()
        title = re.sub(r'<[^>]+>', '', link_m.group(2)).strip()
        snippet_m = re.search(r'<p[^>]*>(.*?)</p>', block, re.DOTALL)
        snippet = re.sub(r'<[^>]+>', ' ', snippet_m.group(1)).strip()[:200] if snippet_m else ""
        if "bing.com" not in url and url.startswith("http"):
            results.append({
                "title": title[:100] or url[:60],
                "url": url,
                "snippet": snippet,
                "source": "bing",
            })

    if not results:
        backup = re.findall(
            r'<h2[^>]*>.*?<a[^>]*href="(https?://(?!.*bing\\.com)[^"]+)"[^>]*>(.*?)</a>',
            html, re.DOTALL
        )
        for href, title in backup:
            if len(results) >= max_results:
                break
            title = re.sub(r'<[^>]+>', '', title).strip()[:100]
            results.append({
                "title": title or href[:60],
                "url": href,
                "snippet": "",
                "source": "bing",
            })
    return results


def search_tavily(query: str, max_results: int = 5,
                  api_key: str = "") -> list[dict]:
    """Tavily 搜索（需 API Key）。"""
    if not api_key:
        return []

    payload = json.dumps({
        "query": query,
        "search_depth": "basic",
        "max_results": max_results,
        "include_answer": True,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.tavily.com/search",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return []

    results = []
    for r in data.get("results", []):
        results.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "snippet": r.get("content", "")[:300],
            "source": "tavily",
        })
    return results


# ── 结果合并 ────────────────────────────────────────────────


def _normalize_url(url: str) -> str:
    """URL 归一化用于去重。"""
    url = url.rstrip("/").lower()
    # 移除 www.、末尾的斜杠、常见追踪参数
    url = re.sub(r'^https?://', '', url)
    url = re.sub(r'^www\d?\.', '', url)
    url = re.sub(r'(\?|&)(utm_source|utm_medium|utm_campaign|utm_term|utm_content|ref|fbclid|gclid)=[^&]+', '', url)
    return url


def merge_results(all_results: list[list[dict]], max_total: int = 10) -> list[dict]:
    """合并多个搜索引擎的结果，去重。"""
    seen_urls: set[str] = set()
    merged: list[dict] = []

    # 按 priority：Tavily > Bing > DDG（Tavily 质量最高优先）
    priority = {"tavily": 0, "bing": 1, "duckduckgo": 2}
    flat = []
    for engine_results in all_results:
        for r in engine_results:
            r["_priority"] = priority.get(r.get("source", ""), 99)
            flat.append(r)

    flat.sort(key=lambda x: x["_priority"])

    for r in flat:
        if len(merged) >= max_total:
            break
        norm_url = _normalize_url(r.get("url", ""))
        if norm_url and norm_url not in seen_urls and len(norm_url) > 5:
            seen_urls.add(norm_url)
            entry = {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("snippet", ""),
                "source": r.get("source", ""),
            }
            # 如果已有相似结果，合并 snippet（取更长的）
            merged.append(entry)

    return merged


# ── 并行搜索 ────────────────────────────────────────────────


def search_all(query: str, max_per_engine: int = 5,
               tavily_api_key: str = "") -> list[dict]:
    """并行搜索所有引擎。"""
    results_lock = threading.Lock()
    all_results: list[list[dict]] = [[], [], []]  # ddg, bing, tavily

    def _run_ddg():
        r = search_duckduckgo(query, max_per_engine)
        with results_lock:
            all_results[0] = r

    def _run_bing():
        r = search_bing(query, max_per_engine)
        with results_lock:
            all_results[1] = r

    def _run_tavily():
        r = search_tavily(query, max_per_engine, tavily_api_key)
        with results_lock:
            all_results[2] = r

    threads = [
        threading.Thread(target=_run_ddg),
        threading.Thread(target=_run_bing),
        threading.Thread(target=_run_tavily),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    return merge_results(all_results, max_total=10)


# ── LLM 汇总 ────────────────────────────────────────────────

AGGREGATE_PROMPT = """你是一个搜索聚合分析器。以下是多个搜索引擎对「{query}」的搜索结果。

请综合所有结果，输出一份结构化回答：

## 综合回答
（用 2-3 句话概括核心信息）

## 要点
- （列出 3-5 个关键信息点）

## 来源
（列出前 5 个最有价值的来源链接）

## 补充说明
（如果有不同来源信息矛盾，在这里指出）

搜索结果：
{results_text}
"""


def aggregate_search(
    query: str,
    llm_chat_fn: Optional[Callable] = None,
    max_per_engine: int = 5,
    tavily_api_key: str = "",
) -> dict:
    """高级聚合搜索：多引擎并行 + LLM 汇总。

    Args:
        query: 搜索关键词
        llm_chat_fn: LLM 聊天函数（用于汇总）。为 None 时返回原始合并结果
        max_per_engine: 每个引擎的最大结果数
        tavily_api_key: Tavily API Key

    Returns:
        {
            "success": bool,
            "output": str,           # 最终输出（LLM 汇总或原始合并）
            "total_sources": int,     # 去重后的来源数
            "engines_used": [str],    # 实际使用的引擎
            "results": [dict],        # 原始合并结果
        }
    """
    start = time.time()

    # 并行搜索
    results = search_all(query, max_per_engine, tavily_api_key)
    engines_used = list({r["source"] for r in results} | {"aggregate"})

    if not results:
        return {
            "success": True,
            "output": f"搜索「{query}」未找到结果。",
            "total_sources": 0,
            "engines_used": engines_used,
            "results": [],
        }

    # 构建原始输出
    lines = [f"🔍 聚合搜索: 「{query}」", f"来源引擎: {', '.join(engines_used)}", ""]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}")
        lines.append(f"   {r['url']}")
        if r["snippet"]:
            lines.append(f"   {r['snippet'][:150]}")
        lines.append(f"   [{r['source']}]")
        lines.append("")

    raw_output = "\n".join(lines).strip()

    # LLM 汇总
    if llm_chat_fn and results:
        results_text = ""
        for i, r in enumerate(results[:8], 1):
            results_text += f"[{i}] {r['title']}\n   URL: {r['url']}\n   摘要: {r['snippet'][:200]}\n   来源: {r['source']}\n\n"

        prompt = AGGREGATE_PROMPT.format(query=query, results_text=results_text)

        try:
            response = llm_chat_fn([
                {"role": "system", "content": "你是夸父的搜索聚合分析器。输出简洁有价值的中文回答。"},
                {"role": "user", "content": prompt},
            ])
            content = ""
            if isinstance(response, dict):
                content = response.get("content", "")
            elif isinstance(response, str):
                content = response

            if content:
                output = content + "\n\n---\n" + raw_output
            else:
                output = raw_output
        except Exception:
            output = raw_output
    else:
        output = raw_output

    elapsed = time.time() - start
    logger.info(f"聚合搜索「{query}」: {len(results)} 结果, {elapsed:.1f}s")

    return {
        "success": True,
        "output": output,
        "total_sources": len(results),
        "engines_used": engines_used,
        "results": results,
    }
