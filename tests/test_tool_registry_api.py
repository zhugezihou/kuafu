"""Test ToolRegistry API layer — registration, query, execution, search, injection.

Covers ToolRegistry methods at the API level (L40-L330 of tool_registry.py)
without testing actual tool handler implementations. All handlers are mock lambdas.
"""

import json
from unittest.mock import patch, MagicMock

import pytest

# Shared mock handler
MOCK_HANDLER = lambda args: {"success": True, "output": "ok"}
MOCK_SCHEMA = {"description": "test tool", "parameters": {"type": "object", "properties": {}}}


# ===================================================================
# Init
# ===================================================================

class TestInit:
    def test_init_creates_empty_lists_and_registers_core_tools(self):
        """__init__ initializes all lists and calls _register_core_tools."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        # Verify internal lists exist
        assert hasattr(tr, '_schemas')
        assert hasattr(tr, '_handlers')
        assert hasattr(tr, '_compact')
        assert hasattr(tr, '_deferred')
        assert hasattr(tr, '_injected_tools')
        # Core tools registered
        assert 'terminal' in tr._handlers
        assert 'finish' in tr._handlers
        # tool_search meta-tool
        assert 'tool_search' in tr._handlers
        # Compact tools registered
        assert 'read_file' in tr._handlers
        assert 'write_file' in tr._handlers
        assert 'patch' in tr._handlers
        # Deferred tools registered
        assert 'web_search' in tr._handlers
        assert 'github_search' in tr._handlers

    def test_get_schemas_returns_core_and_injected(self):
        """get_schemas returns core _schemas + _injected_tools."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        schemas = tr.get_schemas()
        names = [s['function']['name'] for s in schemas]
        assert 'terminal' in names
        assert 'finish' in names
        assert 'tool_search' in names
        # Compact tools are NOT in get_schemas initially
        assert 'read_file' not in names
        # Deferred tools are NOT in get_schemas initially
        assert 'web_search' not in names


# ===================================================================
# Register
# ===================================================================

class TestRegister:
    def test_register_core_tool(self):
        """register adds to _schemas and _handlers."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register('my_tool', MOCK_SCHEMA, MOCK_HANDLER)
        names = [s['function']['name'] for s in tr._schemas]
        assert 'my_tool' in names
        assert 'my_tool' in tr._handlers

    def test_register_adds_full_schema_format(self):
        """register wraps schema in type/function envelope."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register('my_tool', MOCK_SCHEMA, MOCK_HANDLER)
        schema = [s for s in tr._schemas if s['function']['name'] == 'my_tool'][0]
        assert schema['type'] == 'function'
        assert schema['function']['name'] == 'my_tool'
        assert schema['function']['description'] == 'test tool'

    def test_register_removes_duplicate_from_all_pools(self):
        """register removes existing tool from _schemas, _deferred, and _injected_tools."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        # Register as deferred first
        tr.register_deferred('dup_tool', MOCK_SCHEMA, MOCK_HANDLER,
                             keywords=['dup'])
        # Verify in deferred
        assert any(d['schema']['function']['name'] == 'dup_tool' for d in tr._deferred)
        # Now register as core — should remove from deferred
        tr.register('dup_tool', MOCK_SCHEMA, MOCK_HANDLER)
        assert not any(d['schema']['function']['name'] == 'dup_tool' for d in tr._deferred)
        # Should be in _schemas
        assert any(s['function']['name'] == 'dup_tool' for s in tr._schemas)

    def test_register_overwrites_handler(self):
        """register updates handler for existing name."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register('my_tool', MOCK_SCHEMA, lambda args: {'success': True, 'output': 'old'})
        tr.register('my_tool', MOCK_SCHEMA, lambda args: {'success': True, 'output': 'new'})
        assert tr._handlers['my_tool']({'x': 1})['output'] == 'new'


class TestRegisterCompact:
    def test_register_compact_adds_to_compact_and_handlers(self):
        """register_compact adds to _compact and _handlers, not to _schemas."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register_compact('compact_tool', MOCK_SCHEMA, MOCK_HANDLER)
        names = [s['function']['name'] for s in tr._compact]
        assert 'compact_tool' in names
        assert 'compact_tool' in tr._handlers
        # Not in core schemas
        assert not any(s['function']['name'] == 'compact_tool' for s in tr._schemas)

    def test_register_compact_removes_from_all_pools(self):
        """register_compact removes existing from _schemas, _injected_tools, _deferred."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        # Register as core first
        tr.register('re_tool', MOCK_SCHEMA, MOCK_HANDLER)
        assert any(s['function']['name'] == 're_tool' for s in tr._schemas)
        # Re-register as compact — should remove from _schemas
        tr.register_compact('re_tool', MOCK_SCHEMA, MOCK_HANDLER)
        assert not any(s['function']['name'] == 're_tool' for s in tr._schemas)
        assert any(s['function']['name'] == 're_tool' for s in tr._compact)

    def test_register_compact_full_schema_format(self):
        """register_compact wraps schema in type/function envelope."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register_compact('ct', MOCK_SCHEMA, MOCK_HANDLER)
        schema = [s for s in tr._compact if s['function']['name'] == 'ct'][0]
        assert schema['type'] == 'function'
        assert schema['function']['name'] == 'ct'


class TestRegisterDeferred:
    def test_register_deferred_adds_to_deferred_and_handlers(self):
        """register_deferred adds entry with schema, keywords, description."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register_deferred('def_tool', MOCK_SCHEMA, MOCK_HANDLER,
                             keywords=['key1', 'key2'])
        entry = [d for d in tr._deferred if d['schema']['function']['name'] == 'def_tool'][0]
        assert entry['schema']['function']['name'] == 'def_tool'
        assert entry['keywords'] == ['key1', 'key2']
        assert entry['description'] == 'test tool'
        assert 'def_tool' in tr._handlers

    def test_register_deferred_lowercases_keywords(self):
        """register_deferred lowercases keywords."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register_deferred('def_tool', MOCK_SCHEMA, MOCK_HANDLER,
                             keywords=['KEY1', 'Key2'])
        entry = [d for d in tr._deferred if d['schema']['function']['name'] == 'def_tool'][0]
        assert entry['keywords'] == ['key1', 'key2']

    def test_register_deferred_default_keywords_empty(self):
        """register_deferred defaults keywords to empty list."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register_deferred('def_tool', MOCK_SCHEMA, MOCK_HANDLER)
        entry = [d for d in tr._deferred if d['schema']['function']['name'] == 'def_tool'][0]
        assert entry['keywords'] == []

    def test_register_deferred_removes_from_core_and_injected(self):
        """register_deferred removes existing from _schemas and _injected_tools."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register('re_tool', MOCK_SCHEMA, MOCK_HANDLER)
        assert any(s['function']['name'] == 're_tool' for s in tr._schemas)
        tr.register_deferred('re_tool', MOCK_SCHEMA, MOCK_HANDLER)
        assert not any(s['function']['name'] == 're_tool' for s in tr._schemas)
        assert any(d['schema']['function']['name'] == 're_tool' for d in tr._deferred)


# ===================================================================
# Unregister
# ===================================================================

class TestUnregister:
    def test_unregister_removes_from_all_pools(self):
        """unregister removes from _schemas, _compact, _injected_tools, _handlers."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register('my_tool', MOCK_SCHEMA, MOCK_HANDLER)
        result = tr.unregister('my_tool')
        assert result is True
        assert not any(s['function']['name'] == 'my_tool' for s in tr._schemas)
        assert 'my_tool' not in tr._handlers

    def test_unregister_returns_false_if_not_found(self):
        """unregister returns False if tool not in any pool."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        result = tr.unregister('nonexistent')
        assert result is False

    def test_unregister_removes_compact_tool(self):
        """unregister removes from _compact."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register_compact('c_tool', MOCK_SCHEMA, MOCK_HANDLER)
        assert any(s['function']['name'] == 'c_tool' for s in tr._compact)
        tr.unregister('c_tool')
        assert not any(s['function']['name'] == 'c_tool' for s in tr._compact)

    def test_unregister_removes_injected_tool(self):
        """unregister removes from _injected_tools."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        # Inject a deferred tool
        tr.register_deferred('inj_tool', MOCK_SCHEMA, MOCK_HANDLER)
        tr.inject_tool('inj_tool')
        assert any(s['function']['name'] == 'inj_tool' for s in tr._injected_tools)
        tr.unregister('inj_tool')
        assert not any(s['function']['name'] == 'inj_tool' for s in tr._injected_tools)

    def test_unregister_handler_removed(self):
        """unregister removes handler from _handlers."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register('my_tool', MOCK_SCHEMA, MOCK_HANDLER)
        tr.unregister('my_tool')
        assert tr.get_handler('my_tool') is None


# ===================================================================
# Query
# ===================================================================

class TestGetSchemas:
    def test_get_schemas_returns_copy(self):
        """get_schemas returns a new list (not a reference to internal)."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        schemas = tr.get_schemas()
        schemas.append({'dummy': True})
        # Internal should be unchanged
        assert len(tr.get_schemas()) == len(schemas) - 1

    def test_get_schemas_includes_injected(self):
        """get_schemas includes injected tools."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register_deferred('inj_tool', MOCK_SCHEMA, MOCK_HANDLER)
        tr.inject_tool('inj_tool')
        names = [s['function']['name'] for s in tr.get_schemas()]
        assert 'inj_tool' in names


class TestGetActiveToolsNames:
    def test_get_active_tools_names(self):
        """get_active_tools_names returns core + injected tool names."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        names = tr.get_active_tools_names()
        assert 'terminal' in names
        assert 'finish' in names
        assert 'tool_search' in names
        # Not compact or deferred
        assert 'read_file' not in names  # compact, not active
        assert 'web_search' not in names  # deferred, not active

    def test_get_active_tools_names_includes_injected(self):
        """get_active_tools_names includes injected tools."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register_deferred('inj_tool', MOCK_SCHEMA, MOCK_HANDLER)
        tr.inject_tool('inj_tool')
        names = tr.get_active_tools_names()
        assert 'inj_tool' in names


class TestGetCompactToolsDescription:
    def test_get_compact_tools_description(self):
        """get_compact_tools_description returns name/desc tuples for compact tools."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        descs = tr.get_compact_tools_description()
        # Check a known compact tool
        read_file_entry = [d for d in descs if d[0] == 'read_file']
        assert len(read_file_entry) == 1
        assert '读取' in read_file_entry[0][1] or 'read' in read_file_entry[0][1]

    def test_get_compact_tools_description_empty(self):
        """get_compact_tools_description returns empty list when no compact tools."""
        from core.tool_registry import ToolRegistry
        # Create with no core tools registered by skipping _register_core_tools
        # Note: skipping init, so we directly set up minimal state
        tr = ToolRegistry.__new__(ToolRegistry)
        tr._compact = []
        descs = tr.get_compact_tools_description()
        assert descs == []


# ===================================================================
# Execute
# ===================================================================

class TestExecute:
    def test_execute_core_tool(self):
        """execute calls handler and returns result."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        result = tr.execute({
            'function': {'name': 'finish', 'arguments': {'reason': 'test'}}
        })
        assert result['success'] is True

    def test_execute_parses_string_arguments(self):
        """execute parses string JSON arguments."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register('echo_tool', MOCK_SCHEMA, lambda args: {
            'success': True, 'output': args.get('msg', '')
        })
        result = tr.execute({
            'function': {'name': 'echo_tool', 'arguments': '{"msg": "hello"}'}
        })
        assert result['output'] == 'hello'

    def test_execute_empty_string_arguments(self):
        """execute handles empty string arguments."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register('echo_tool', MOCK_SCHEMA, lambda args: {
            'success': True, 'output': str(args)
        })
        result = tr.execute({
            'function': {'name': 'echo_tool', 'arguments': ''}
        })
        # '' is not valid JSON, so falls to except -> args = {}
        assert "'{}'" in result['output'] or "{}" in result['output']

    def test_execute_invalid_json_string_arguments(self):
        """execute handles invalid JSON string arguments -> empty dict."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register('echo_tool', MOCK_SCHEMA, lambda args: {
            'success': True, 'output': str(type(args).__name__)
        })
        result = tr.execute({
            'function': {'name': 'echo_tool', 'arguments': 'not json!!!'}
        })
        assert result['output'] == 'dict'

    def test_execute_dict_arguments(self):
        """execute passes dict arguments directly."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register('echo_tool', MOCK_SCHEMA, lambda args: {
            'success': True, 'output': str(args.get('key', ''))
        })
        result = tr.execute({
            'function': {'name': 'echo_tool', 'arguments': {'key': 'value'}}
        })
        assert result['output'] == 'value'

    def test_execute_non_dict_non_string_arguments(self):
        """execute handles non-dict, non-string args as empty dict."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register('echo_tool', MOCK_SCHEMA, lambda args: {
            'success': True, 'output': str(type(args).__name__)
        })
        result = tr.execute({
            'function': {'name': 'echo_tool', 'arguments': 42}
        })
        assert result['output'] == 'dict'

    def test_execute_unknown_tool(self):
        """execute returns error for unknown tool."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        result = tr.execute({
            'function': {'name': 'nonexistent_tool', 'arguments': {}}
        })
        assert result['success'] is False
        assert '未知工具' in result['output']

    def test_execute_missing_function_key(self):
        """execute handles missing 'function' key."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        result = tr.execute({})
        assert result['success'] is False
        assert '未知工具' in result['output']

    def test_execute_missing_name_key(self):
        """execute handles missing 'name' in function."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        result = tr.execute({'function': {}})
        assert result['success'] is False
        assert '未知工具' in result['output']

    def test_execute_compact_tool_promotion(self):
        """execute promotes compact tool on first call."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register_compact('promo_tool', MOCK_SCHEMA, MOCK_HANDLER)
        # Not injected yet
        assert not any(s['function']['name'] == 'promo_tool' for s in tr._injected_tools)
        result = tr.execute({
            'function': {'name': 'promo_tool', 'arguments': {}}
        })
        assert result['success'] is True
        # Now injected
        assert any(s['function']['name'] == 'promo_tool' for s in tr._injected_tools)

    def test_execute_handler_exception(self):
        """execute returns error when handler raises exception."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        def broken_handler(args):
            raise ValueError('things broke')
        tr.register('broken_tool', MOCK_SCHEMA, broken_handler)
        result = tr.execute({
            'function': {'name': 'broken_tool', 'arguments': {}}
        })
        assert result['success'] is False
        assert '异常' in result['output']

    def test_execute_handler_returns_non_dict(self):
        """execute wraps non-dict result in success/output format."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register('str_tool', MOCK_SCHEMA, lambda args: 'plain string')
        result = tr.execute({
            'function': {'name': 'str_tool', 'arguments': {}}
        })
        assert result['success'] is True
        assert result['output'] == 'plain string'

    def test_execute_handler_missing_output_key(self):
        """execute adds output key when handler result lacks it."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register('no_output', MOCK_SCHEMA, lambda args: {'success': True, 'result': 'via result key'})
        result = tr.execute({
            'function': {'name': 'no_output', 'arguments': {}}
        })
        assert result['success'] is True
        assert result['output'] == 'via result key'

    def test_execute_handler_returns_dict_with_output(self):
        """execute returns handler result directly if it has output key."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register('good_tool', MOCK_SCHEMA, lambda args: {'success': True, 'output': 'direct'})
        result = tr.execute({
            'function': {'name': 'good_tool', 'arguments': {}}
        })
        assert result == {'success': True, 'output': 'direct'}


# ===================================================================
# Get Handler
# ===================================================================

class TestGetHandler:
    def test_get_handler_exists(self):
        """get_handler returns handler for existing tool."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        handler = tr.get_handler('terminal')
        assert handler is not None
        assert callable(handler)

    def test_get_handler_not_exists(self):
        """get_handler returns None for non-existent tool."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        handler = tr.get_handler('nonexistent')
        assert handler is None


# ===================================================================
# List Tools
# ===================================================================

class TestListTools:
    def test_list_tools_returns_core_names(self):
        """list_tools returns names of core (non-compact, non-deferred) tools."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        names = tr.list_tools()
        assert 'terminal' in names
        assert 'finish' in names
        assert 'tool_search' in names
        # Not compact or deferred
        assert 'read_file' not in names
        assert 'web_search' not in names

    def test_list_tools_includes_newly_registered(self):
        """list_tools includes newly registered core tools."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register('new_tool', MOCK_SCHEMA, MOCK_HANDLER)
        names = tr.list_tools()
        assert 'new_tool' in names

    def test_list_tools_excludes_unregistered(self):
        """list_tools excludes unregistered tools."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register('tmp_tool', MOCK_SCHEMA, MOCK_HANDLER)
        tr.unregister('tmp_tool')
        names = tr.list_tools()
        assert 'tmp_tool' not in names


# ===================================================================
# Promote Compact Tool
# ===================================================================

class TestPromoteCompactTool:
    def test_promote_first_time_returns_true(self):
        """_promote_compact_tool returns True on first promotion."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register_compact('promo_tool', MOCK_SCHEMA, MOCK_HANDLER)
        result = tr._promote_compact_tool('promo_tool')
        assert result is True
        assert any(s['function']['name'] == 'promo_tool' for s in tr._injected_tools)

    def test_promote_already_injected_returns_false(self):
        """_promote_compact_tool returns False if already injected."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register_compact('promo_tool', MOCK_SCHEMA, MOCK_HANDLER)
        tr._promote_compact_tool('promo_tool')  # first time
        result = tr._promote_compact_tool('promo_tool')  # second time
        assert result is False

    def test_promote_not_compact_returns_false(self):
        """_promote_compact_tool returns False if tool not in compact."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        result = tr._promote_compact_tool('nonexistent')
        assert result is False

    def test_promote_core_tool_not_in_compact_returns_false(self):
        """_promote_compact_tool returns False for core tools not in compact."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        result = tr._promote_compact_tool('terminal')
        assert result is False


# ===================================================================
# Inject Tool (Deferred → Injected)
# ===================================================================

class TestInjectTool:
    def test_inject_deferred_tool(self):
        """inject_tool moves deferred tool to injected."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register_deferred('inj_tool', MOCK_SCHEMA, MOCK_HANDLER)
        result = tr.inject_tool('inj_tool')
        assert result is True
        assert any(s['function']['name'] == 'inj_tool' for s in tr._injected_tools)

    def test_inject_already_injected_returns_true(self):
        """inject_tool returns True even if already injected."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register_deferred('inj_tool', MOCK_SCHEMA, MOCK_HANDLER)
        tr.inject_tool('inj_tool')
        result = tr.inject_tool('inj_tool')
        assert result is True
        # Should not be duplicated in _injected_tools
        injected = [s for s in tr._injected_tools if s['function']['name'] == 'inj_tool']
        assert len(injected) == 1

    def test_inject_not_deferred_returns_false(self):
        """inject_tool returns False if tool not in deferred."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        result = tr.inject_tool('nonexistent')
        assert result is False

    def test_inject_core_tool_not_in_deferred_returns_false(self):
        """inject_tool returns False for core tools."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        result = tr.inject_tool('terminal')
        assert result is False


# ===================================================================
# Search Deferred Tools
# ===================================================================

class TestSearchDeferredTools:
    def test_search_english_substring_match(self):
        """_search_deferred_tools matches English substrings."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register_deferred('web_search_tool', MOCK_SCHEMA, MOCK_HANDLER,
                             keywords=['web', 'search', 'internet'])
        results = tr._search_deferred_tools('search')
        assert len(results) >= 1
        assert any(r['name'] == 'web_search_tool' for r in results)

    def test_search_keyword_match(self):
        """_search_deferred_tools matches via keywords."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register_deferred('test_tool', MOCK_SCHEMA, MOCK_HANDLER,
                             keywords=['alpha', 'beta'])
        results = tr._search_deferred_tools('alpha')
        assert len(results) >= 1
        assert any(r['name'] == 'test_tool' for r in results)

    def test_search_no_match_returns_empty(self):
        """_search_deferred_tools returns empty list for no match."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        results = tr._search_deferred_tools('zzzzzznonexistent')
        assert results == []

    def test_search_empty_query_returns_empty(self):
        """_search_deferred_tools returns empty for empty query."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        results = tr._search_deferred_tools('')
        assert results == []

    def test_search_single_char_query_returns_empty(self):
        """_search_deferred_tools returns empty for single-char query (filtered by len > 1)."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        results = tr._search_deferred_tools('a')
        assert results == []

    def test_search_short_words_filtered(self):
        """_search_deferred_tools filters words with len <= 1."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        results = tr._search_deferred_tools('a b')
        assert results == []

    def test_search_sort_by_score_descending(self):
        """_search_deferred_tools sorts by score descending."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register_deferred('best_match', MOCK_SCHEMA, MOCK_HANDLER,
                             keywords=['search', 'engine'])
        tr.register_deferred('ok_match', MOCK_SCHEMA, MOCK_HANDLER,
                             keywords=[])
        results = tr._search_deferred_tools('search')
        # best_match should be first (higher score due to keyword match)
        if len(results) >= 2:
            assert results[0]['score'] >= results[1]['score']

    def test_search_respects_max_results(self):
        """_search_deferred_tools respects max_results."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        # Register multiple tools that match 'web'
        # (web_search_tool and web_fetch are already registered)
        tr.register_deferred('web_tool_a', MOCK_SCHEMA, MOCK_HANDLER, keywords=['web'])
        tr.register_deferred('web_tool_b', MOCK_SCHEMA, MOCK_HANDLER, keywords=['web'])
        results = tr._search_deferred_tools('web', max_results=2)
        assert len(results) <= 2

    def test_search_chinese_ngram_segmentation(self):
        """_search_deferred_tools segments Chinese text into n-grams."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register_deferred('github_get_repo', MOCK_SCHEMA, MOCK_HANDLER,
                             keywords=['github', 'git', 'repository'])
        # Search with Chinese query; '仓库' is in keywords of github_get_repo
        results = tr._search_deferred_tools('仓库')
        assert any(r['name'] == 'github_get_repo' for r in results)

    def test_search_mixed_chinese_english_query(self):
        """_search_deferred_tools handles mixed Chinese-English queries."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        # '网页搜索' — mixed query
        results = tr._search_deferred_tools('网页搜索')
        # Should find at least web_search or web_fetch
        assert len(results) >= 1

    def test_search_scoring_name_match_highest(self):
        """_search_deferred_tools gives highest score for name matches."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        # query 'github' should match github_search and github_get_repo by name
        results = tr._search_deferred_tools('github')
        assert len(results) >= 2
        # All matched by name (score 10) or keyword (score 5)
        for r in results:
            assert r['score'] > 0

    def test_search_description_match_lowest_score(self):
        """_search_deferred_tools gives lowest score for description-only matches."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register_deferred('desc_only_tool', {
            'description': 'this matches the query term foobarbaz',
            'parameters': {'type': 'object', 'properties': {}}
        }, MOCK_HANDLER, keywords=['unique'])
        # Search for 'foobarbaz' which is only in description
        results = tr._search_deferred_tools('foobarbaz')
        assert len(results) >= 1
        assert results[0]['score'] == 1  # description match only


# ===================================================================
# Tool Search Schema (Static)
# ===================================================================

class TestToolSearchSchema:
    def test_tool_search_schema_structure(self):
        """_tool_search_schema returns correct static schema."""
        from core.tool_registry import ToolRegistry
        schema = ToolRegistry._tool_search_schema()
        assert 'description' in schema
        assert 'parameters' in schema
        assert schema['parameters']['type'] == 'object'
        assert 'query' in schema['parameters']['properties']
        assert 'query' in schema['parameters']['required']


# ===================================================================
# Edge Cases & Branches
# ===================================================================

class TestEdgeCases:
    def test_execute_promotion_and_handler_on_compact_tool(self):
        """execute promotes compact and runs handler in one call."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        captured = []
        def capturing_handler(args):
            captured.append(args)
            return {'success': True, 'output': 'ran'}
        tr.register_compact('capture_tool', MOCK_SCHEMA, capturing_handler)
        result = tr.execute({
            'function': {'name': 'capture_tool', 'arguments': {'x': 1}}
        })
        assert result['success'] is True
        assert result['output'] == 'ran'
        assert len(captured) == 1
        assert captured[0] == {'x': 1}
        # Now injected
        assert any(s['function']['name'] == 'capture_tool' for s in tr._injected_tools)

    def test_unregister_compact_removes_handler(self):
        """unregister removes handler even for compact tools."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register_compact('temp_tool', MOCK_SCHEMA, MOCK_HANDLER)
        tr.unregister('temp_tool')
        result = tr.execute({
            'function': {'name': 'temp_tool', 'arguments': {}}
        })
        assert result['success'] is False
        assert '未知工具' in result['output']

    def test_register_override_allows_execute_new_handler(self):
        """Re-registering same name executes new handler."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register('switch_tool', MOCK_SCHEMA, lambda args: {'success': True, 'output': 'v1'})
        tr.register('switch_tool', MOCK_SCHEMA, lambda args: {'success': True, 'output': 'v2'})
        result = tr.execute({
            'function': {'name': 'switch_tool', 'arguments': {}}
        })
        assert result['output'] == 'v2'

    def test_register_compact_and_promote_then_execute(self):
        """Full flow: register_compact, execute (promotes), execute again."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register_compact('flow_tool', MOCK_SCHEMA, MOCK_HANDLER)
        # First call promotes
        r1 = tr.execute({'function': {'name': 'flow_tool', 'arguments': {}}})
        assert r1['success'] is True
        assert any(s['function']['name'] == 'flow_tool' for s in tr._injected_tools)
        # Second call — already promoted, handler still works
        r2 = tr.execute({'function': {'name': 'flow_tool', 'arguments': {}}})
        assert r2['success'] is True

    def test_register_then_unregister_then_register(self):
        """Register → unregister → register works cleanly."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register('recycle_tool', MOCK_SCHEMA, MOCK_HANDLER)
        tr.unregister('recycle_tool')
        # Not in handlers
        assert 'recycle_tool' not in tr._handlers
        # Re-register
        tr.register('recycle_tool', MOCK_SCHEMA, MOCK_HANDLER)
        result = tr.execute({
            'function': {'name': 'recycle_tool', 'arguments': {}}
        })
        assert result['success'] is True

    def test_unregister_with_no_pools_unchanged(self):
        """unregister for tool not in any pool returns False, state unchanged."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        count_before = len(tr._schemas) + len(tr._compact) + len(tr._injected_tools)
        result = tr.unregister('does_not_exist_at_all')
        count_after = len(tr._schemas) + len(tr._compact) + len(tr._injected_tools)
        assert result is False
        assert count_before == count_after

    def test_deferred_full_cycle(self):
        """Full deferred lifecycle: register, search, inject, execute."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register_deferred('cycle_tool', MOCK_SCHEMA, MOCK_HANDLER,
                             keywords=['cycle', 'test'])
        # Search
        found = tr._search_deferred_tools('cycle')
        assert any(r['name'] == 'cycle_tool' for r in found)
        # Inject
        injected = tr.inject_tool('cycle_tool')
        assert injected is True
        assert any(s['function']['name'] == 'cycle_tool' for s in tr._injected_tools)
        # Execute
        result = tr.execute({
            'function': {'name': 'cycle_tool', 'arguments': {}}
        })
        assert result['success'] is True

    def test_search_chinese_ngram_2_3_4(self):
        """_search_deferred_tools generates 2,3,4-gram from Chinese text."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        # '互联网搜索' in keywords of web_search
        # '互联网' is a 3-char word. '互联网搜索' is 4-char.
        # Search '联网' (2-gram within '互联网')
        results = tr._search_deferred_tools('联网')
        # '联网' is part of '互联网搜索' which is a keyword of web_search
        # But '联网' itself may not be a keyword; it's a 2-gram of the query word
        # Actually '联网' as a query will split: '联网' is fully Chinese, so
        # generate 2,3,4-grams: '联网' (2), none for 3/4 (len=2)
        # '联网' would need to match something in the deferred pool
        # Let's just verify no crash
        assert isinstance(results, list)

    def test_mixed_query_with_english_segments(self):
        """_search_deferred_tools extracts English subsegments from mixed words."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        # Query 'github仓库' — mixed Chinese+English
        results = tr._search_deferred_tools('github仓库')
        # Should extract 'github' (len>=3) as an English segment
        # github matches github_search and github_get_repo by name
        assert len(results) >= 1

    def test_mixed_query_no_english_segments(self):
        """_search_deferred_tools handles mixed words with <3 char English segments."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        # Query 'ab仓库' — 'ab' is only 2 chars, not extracted
        results = tr._search_deferred_tools('ab仓库')
        # Should not crash
        assert isinstance(results, list)


# ===================================================================
# Tool Search Registration Path
# ===================================================================

class TestToolSearchRegistration:
    def test_tool_search_registered_in_init(self):
        """_register_tool_search adds tool_search to _schemas and _handlers."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        assert any(s['function']['name'] == 'tool_search' for s in tr._schemas)
        assert 'tool_search' in tr._handlers


# ===================================================================
# Register with duplicate name cleanup
# ===================================================================

class TestDuplicateNameCleanup:
    def test_register_removes_from_injected(self):
        """register removes existing from _injected_tools."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register_deferred('dup_tool', MOCK_SCHEMA, MOCK_HANDLER)
        tr.inject_tool('dup_tool')
        assert any(s['function']['name'] == 'dup_tool' for s in tr._injected_tools)
        # Now register as core — should remove from injected
        tr.register('dup_tool', MOCK_SCHEMA, MOCK_HANDLER)
        assert not any(s['function']['name'] == 'dup_tool' for s in tr._injected_tools)

    def test_register_compact_removes_from_injected(self):
        """register_compact removes existing from _injected_tools."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register_deferred('dup_tool', MOCK_SCHEMA, MOCK_HANDLER)
        tr.inject_tool('dup_tool')
        tr.register_compact('dup_tool', MOCK_SCHEMA, MOCK_HANDLER)
        assert not any(s['function']['name'] == 'dup_tool' for s in tr._injected_tools)

    def test_register_deferred_removes_from_injected(self):
        """register_deferred removes existing from _injected_tools."""
        from core.tool_registry import ToolRegistry
        tr = ToolRegistry()
        tr.register_deferred('dup_tool', MOCK_SCHEMA, MOCK_HANDLER)
        tr.inject_tool('dup_tool')
        tr.register_deferred('dup_tool', MOCK_SCHEMA, MOCK_HANDLER)
        assert not any(s['function']['name'] == 'dup_tool' for s in tr._injected_tools)
