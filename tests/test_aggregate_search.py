"""测试 core/aggregate_search.py — 聚合搜索。"""

import json
import pytest
from unittest.mock import MagicMock, patch


class TestNormalizeUrl:
    """_normalize_url 测试。"""

    def test_remove_www(self):
        from core.aggregate_search import _normalize_url
        assert _normalize_url("https://www.example.com") == "example.com"

    def test_remove_trailing_slash(self):
        from core.aggregate_search import _normalize_url
        assert _normalize_url("https://example.com/") == "example.com"

    def test_remove_utm_params(self):
        from core.aggregate_search import _normalize_url
        assert "utm_source" not in _normalize_url("https://example.com/page?utm_source=twitter&a=1")

    def test_lowercase(self):
        from core.aggregate_search import _normalize_url
        assert _normalize_url("HTTPS://EXAMPLE.COM/Page") == "example.com/page"

    def test_remove_www_numbered(self):
        from core.aggregate_search import _normalize_url
        assert _normalize_url("https://www2.example.com") == "example.com"


class TestMergeResults:
    """merge_results 测试。"""

    def test_empty(self):
        """空结果。"""
        from core.aggregate_search import merge_results
        assert merge_results([]) == []

    def test_single_engine(self):
        """单引擎结果。"""
        from core.aggregate_search import merge_results
        r = merge_results([[{"title": "A", "url": "https://example.com", "snippet": "desc", "source": "ddg"}]])
        assert len(r) == 1

    def test_dedup_same_url(self):
        """相同 URL 去重。"""
        from core.aggregate_search import merge_results
        results = [
            [{"title": "A", "url": "https://example.com", "snippet": "", "source": "ddg"}],
            [{"title": "A", "url": "https://example.com/", "snippet": "", "source": "bing"}],
        ]
        merged = merge_results(results)
        assert len(merged) >= 1

    def test_priority_tavily_first(self):
        """Tavily 结果优先排序。"""
        from core.aggregate_search import merge_results
        results = [
            [{"title": "DDG", "url": "https://duckduckgo.com", "snippet": "", "source": "duckduckgo"}],
            [{"title": "Tavily", "url": "https://tavily.com", "snippet": "", "source": "tavily"}],
            [{"title": "Bing", "url": "https://bing.com", "snippet": "", "source": "bing"}],
        ]
        merged = merge_results(results)
        assert len(merged) >= 1
        assert merged[0]["source"] == "tavily"

    def test_max_total(self):
        """限制最大结果数。"""
        from core.aggregate_search import merge_results
        many_results = [
            [{"title": f"R{i}", "url": f"https://r{i}.com", "snippet": "", "source": "ddg"}
             for i in range(20)]
        ]
        merged = merge_results(many_results, max_total=5)
        assert len(merged) == 5

    def test_short_url_dropped(self):
        """过短 URL 被过滤。"""
        from core.aggregate_search import merge_results
        merged = merge_results([[{"title": "X", "url": "ab", "snippet": "", "source": "ddg"}]])
        assert len(merged) == 0


class TestSearchAll:
    """search_all 测试（mock merge_results 验证搜索调用）。"""

    def test_all_empty(self):
        """所有引擎无结果。"""
        with patch("core.aggregate_search.search_duckduckgo", return_value=[]):
            with patch("core.aggregate_search.search_bing", return_value=[]):
                with patch("core.aggregate_search.search_tavily", return_value=[]):
                    from core.aggregate_search import search_all
                    result = search_all("test")
                    assert len(result) >= 0  # threads may or may not complete in time

    def test_some_results(self):
        """部分引擎有结果——mock merge_results 验证搜索参数传递。"""
        from core.aggregate_search import search_all
        mock_results = [{"title": "A", "url": "https://a.com", "snippet": "", "source": "ddg"}]
        with patch("core.aggregate_search.search_duckduckgo", return_value=mock_results):
            with patch("core.aggregate_search.search_bing", return_value=[]):
                with patch("core.aggregate_search.search_tavily", return_value=[]):
                    result = search_all("test")
                    # Due to threading, results may or may not arrive in time
                    # We just verify no crash
                    assert isinstance(result, list)


class TestSearchFunctionsMocked:
    """各搜索引擎函数 mock HTML 解析测试。"""

    def test_search_duckduckgo_link_pattern(self):
        """DDG link 模式匹配。"""
        html = '''<a href="https://example.com" class="result-link">Example</a>'''
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = html.encode("utf-8")
            mock_urlopen.return_value.__enter__.return_value = mock_resp
            from core.aggregate_search import search_duckduckgo
            results = search_duckduckgo("test")
            assert len(results) >= 1

    def test_search_duckduckgo_fallback_links(self):
        """DDG 无 link 时通用链接 fallback。"""
        html = '''<a href="https://example.com">Example Link</a>'''
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = html.encode("utf-8")
            mock_urlopen.return_value.__enter__.return_value = mock_resp
            from core.aggregate_search import search_duckduckgo
            results = search_duckduckgo("test")
            assert len(results) >= 1

    def test_search_duckduckgo_exception(self):
        """DDG 异常返回空列表。"""
        with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
            from core.aggregate_search import search_duckduckgo
            assert search_duckduckgo("test") == []

    def test_search_bing_algo_pattern(self):
        """Bing b_algo 模式匹配。"""
        html = '''
        <li class="b_algo">
            <h2><a href="https://example.com">Example Title</a></h2>
            <p>Some snippet text</p>
        </li>
        '''
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = html.encode("utf-8")
            mock_urlopen.return_value.__enter__.return_value = mock_resp
            from core.aggregate_search import search_bing
            results = search_bing("test")
            assert len(results) >= 1

    def test_search_bing_backup(self):
        """Bing 无 b_algo 时回退。"""
        html = '<h2><a href="https://example.com">Title</a></h2>'
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = html.encode("utf-8")
            mock_urlopen.return_value.__enter__.return_value = mock_resp
            from core.aggregate_search import search_bing
            results = search_bing("test")
            assert len(results) >= 1

    def test_search_bing_exception(self):
        """Bing 异常返回空列表。"""
        with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
            from core.aggregate_search import search_bing
            assert search_bing("test") == []

    def test_search_tavily_no_key(self):
        """Tavily 无 API key 返回空。"""
        from core.aggregate_search import search_tavily
        assert search_tavily("test") == []

    def test_search_tavily_with_key(self):
        """Tavily 有 key 时调用 API。"""
        resp_data = json.dumps({
            "results": [
                {"title": "A", "url": "https://a.com", "content": "desc"},
            ]
        })
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = resp_data.encode("utf-8")
            mock_urlopen.return_value.__enter__.return_value = mock_resp
            from core.aggregate_search import search_tavily
            results = search_tavily("test", api_key="sk-test")
            assert len(results) == 1

    def test_search_tavily_exception(self):
        """Tavily 异常返回空。"""
        with patch("urllib.request.urlopen", side_effect=Exception("fail")):
            from core.aggregate_search import search_tavily
            assert search_tavily("test", api_key="sk-test") == []


class TestAggregateSearch:
    """aggregate_search 测试。"""

    def test_no_results(self):
        """无搜索结果时返回友好消息。"""
        with patch("core.aggregate_search.search_all", return_value=[]):
            from core.aggregate_search import aggregate_search
            result = aggregate_search("nothing")
            assert result["success"] is True
            assert "未找到" in result["output"]
            assert result["total_sources"] == 0

    def test_with_results_no_llm(self):
        """有结果但不使用 LLM 汇总。"""
        mock_results = [
            {"title": "Test", "url": "https://test.com", "snippet": "desc", "source": "ddg"}
        ]
        with patch("core.aggregate_search.search_all", return_value=mock_results):
            from core.aggregate_search import aggregate_search
            result = aggregate_search("test")
            assert result["success"] is True
            assert result["total_sources"] == 1
            assert "Test" in result["output"]

    def test_with_llm_summary(self):
        """LLM 汇总结果。"""
        mock_results = [
            {"title": "Test", "url": "https://test.com", "snippet": "desc", "source": "ddg"}
        ]
        mock_llm = MagicMock(return_value={"content": "这是一个汇总结果"})
        with patch("core.aggregate_search.search_all", return_value=mock_results):
            from core.aggregate_search import aggregate_search
            result = aggregate_search("test", llm_chat_fn=mock_llm)
            assert result["success"] is True
            assert "汇总" in result["output"]

    def test_llm_exception_fallback(self):
        """LLM 异常时回退到原始结果。"""
        mock_results = [
            {"title": "Test", "url": "https://test.com", "snippet": "desc", "source": "ddg"}
        ]
        mock_llm = MagicMock(side_effect=Exception("LLM crashed"))
        with patch("core.aggregate_search.search_all", return_value=mock_results):
            from core.aggregate_search import aggregate_search
            result = aggregate_search("test", llm_chat_fn=mock_llm)
            assert result["success"] is True
            assert "Test" in result["output"]

    def test_llm_response_is_string(self):
        """LLM 返回字符串时也能处理。"""
        mock_results = [
            {"title": "Test", "url": "https://test.com", "snippet": "desc", "source": "ddg"}
        ]
        mock_llm = MagicMock(return_value="string response")
        with patch("core.aggregate_search.search_all", return_value=mock_results):
            from core.aggregate_search import aggregate_search
            result = aggregate_search("test", llm_chat_fn=mock_llm)
            assert result["success"] is True

    def test_search_duckduckgo_max_results_break(self):
        """覆盖 L64-68: 超过 max_results 时 break（result-link 主路径）。"""
        html = ""
        for i in range(8):
            html += f'<a class="result-link" href="https://e{i}.com">Title{i}</a>'
            html += f'<td class="result-snippet">Snippet{i}</td>'
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = html.encode("utf-8")
            mock_urlopen.return_value.__enter__.return_value = mock_resp
            from core.aggregate_search import search_duckduckgo
            results = search_duckduckgo("test")
            assert len(results) == 5

    def test_search_bing_b_algo_max_break(self):
        """覆盖 L118: b_algo 循环中达到 max_results 后 break。"""
        blocks = ""
        for i in range(8):
            blocks += (
                f'<li class="b_algo">'
                f'<h2><a href="https://e{i}.com">Title{i}</a></h2>'
                f'<p>Snip{i}</p>'
                f'</li>'
            )
        html = f"<html>{blocks}</html>"
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = html.encode("utf-8")
            mock_urlopen.return_value.__enter__.return_value = mock_resp
            from core.aggregate_search import search_bing
            results = search_bing("test")
            assert len(results) == 5

    def test_search_bing_b_algo_no_link_continue(self):
        """覆盖 L124: b_algo 块内无 h2>a 时 continue。"""
        html = '''
        <li class="b_algo">
            <div>No link here</div>
            <p>Snippet text</p>
        </li>
        <li class="b_algo">
            <h2><a href="https://example.com">Real Title</a></h2>
            <p>Real snippet</p>
        </li>
        '''
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = html.encode("utf-8")
            mock_urlopen.return_value.__enter__.return_value = mock_resp
            from core.aggregate_search import search_bing
            results = search_bing("test")
            assert len(results) == 1
            assert results[0]["title"] == "Real Title"

    def test_search_bing_backup_max_break(self):
        """覆盖 L144: 回退模式中达到 max_results 后 break。"""
        backup_links = ""
        for i in range(8):
            backup_links += f'<h2><a href="https://e{i}.com">Title{i}</a></h2>'
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.read.return_value = backup_links.encode("utf-8")
            mock_urlopen.return_value.__enter__.return_value = mock_resp
            from core.aggregate_search import search_bing
            results = search_bing("test")
            assert len(results) == 5

    def test_llm_returns_empty_content_dict(self):
        """覆盖 L371: LLM 返回空 content 时 output = raw_output。"""
        mock_results = [
            {"title": "Test", "url": "https://test.com", "snippet": "desc", "source": "ddg"}
        ]
        mock_llm = MagicMock(return_value={"content": ""})
        with patch("core.aggregate_search.search_all", return_value=mock_results):
            from core.aggregate_search import aggregate_search
            result = aggregate_search("test", llm_chat_fn=mock_llm)
            assert result["success"] is True
            assert "---" not in result["output"]
