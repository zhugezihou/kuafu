"""
tests/test_agent_tree.py — Agent 树系统测试
"""

import pytest
from core.agent_tree import (
    AgentPath, AgentStatus, LiveAgent,
    AgentRegistry, AgentTree,
)


# =========================================================================
# AgentPath 测试
# =========================================================================

class TestAgentPath:

    def test_root(self):
        path = AgentPath.root()
        assert path.is_root()
        assert str(path) == "/"
        assert path.name == "/"

    def test_parse_absolute(self):
        path = AgentPath.parse("/child/grand")
        assert str(path) == "/child/grand"
        assert path.name == "grand"

    def test_parse_simple(self):
        path = AgentPath.parse("child")
        assert str(path) == "/child"

    def test_parent(self):
        path = AgentPath.parse("/child/grand")
        parent = path.parent()
        assert str(parent) == "/child"
        assert parent.is_root() is False

    def test_parent_of_root(self):
        path = AgentPath.root()
        parent = path.parent()
        assert parent.is_root()

    def test_child(self):
        path = AgentPath.root().child("child1")
        assert str(path) == "/child1"

    def test_resolve_parent(self):
        path = AgentPath.parse("/child/grand")
        resolved = path.resolve("..")
        assert str(resolved) == "/child"

    def test_resolve_absolute(self):
        path = AgentPath.parse("/child")
        resolved = path.resolve("/other")
        assert str(resolved) == "/other"

    def test_eq(self):
        assert AgentPath.parse("/a/b") == AgentPath.parse("/a/b")
        assert AgentPath.parse("/a") != AgentPath.parse("/b")

    def test_hash(self):
        s = {AgentPath.parse("/a"), AgentPath.parse("/a")}
        assert len(s) == 1


# =========================================================================
# LiveAgent 测试
# =========================================================================

class TestLiveAgent:

    def test_create_root(self):
        agent = LiveAgent("root", AgentPath.root())
        assert agent.status == AgentStatus.IDLE
        assert agent.name == "root"
        assert agent.path.is_root()

    def test_create_child(self):
        path = AgentPath.parse("/child")
        agent = LiveAgent("child", path, parent_path=AgentPath.root())
        assert str(agent.parent_path) == "/"

    def test_status_change(self):
        agent = LiveAgent("test", AgentPath.parse("/test"))
        changes = []
        agent.on_status_change(lambda a, o, n: changes.append((o.value, n.value)))

        agent.set_status(AgentStatus.RUNNING)
        assert agent.status == AgentStatus.RUNNING
        assert len(changes) == 1
        assert changes[0] == ("idle", "running")

    def test_to_dict(self):
        agent = LiveAgent("test", AgentPath.parse("/test"),
                          metadata={"version": "1.0"})
        d = agent.to_dict()
        assert d["name"] == "test"
        assert d["path"] == "/test"
        assert d["metadata"]["version"] == "1.0"


# =========================================================================
# AgentRegistry 测试
# =========================================================================

class TestAgentRegistry:

    def setup_method(self):
        self.reg = AgentRegistry()

    def test_register_and_get(self):
        agent = LiveAgent("root", AgentPath.root())
        assert self.reg.register(agent) is True
        assert self.reg.get(AgentPath.root()) is agent

    def test_register_duplicate(self):
        a1 = LiveAgent("a1", AgentPath.parse("/x"))
        a2 = LiveAgent("a2", AgentPath.parse("/x"))
        assert self.reg.register(a1) is True
        assert self.reg.register(a2) is False  # 重复

    def test_unregister(self):
        agent = LiveAgent("test", AgentPath.parse("/test"))
        self.reg.register(agent)
        assert self.reg.unregister(AgentPath.parse("/test")) is True
        assert self.reg.get(AgentPath.parse("/test")) is None

    def test_list_children(self):
        root = LiveAgent("root", AgentPath.root())
        self.reg.register(root)

        c1 = LiveAgent("c1", AgentPath.parse("/c1"), parent_path=AgentPath.root())
        c2 = LiveAgent("c2", AgentPath.parse("/c2"), parent_path=AgentPath.root())
        gc = LiveAgent("gc", AgentPath.parse("/c1/gc"))
        self.reg.register(c1)
        self.reg.register(c2)
        self.reg.register(gc)

        children = self.reg.list_children(AgentPath.root())
        assert len(children) == 2  # c1 和 c2，不包括 gc

    def test_get_root(self):
        root = LiveAgent("root", AgentPath.root())
        self.reg.register(root)
        assert self.reg.get_root() is root

    def test_get_by_path_str(self):
        agent = LiveAgent("test", AgentPath.parse("/test"))
        self.reg.register(agent)
        assert self.reg.get_by_path_str("/test") is agent
        assert self.reg.get_by_path_str("/nonexistent") is None

    def test_tree_diagram(self):
        root = LiveAgent("夸父", AgentPath.root())
        self.reg.register(root)
        diagram = self.reg.get_tree_diagram()
        assert "夸父" in diagram
        assert "1 agents" in diagram


# =========================================================================
# AgentTree 高层 API 测试
# =========================================================================

class TestAgentTree:

    def setup_method(self):
        self.tree = AgentTree()

    def test_init_root(self):
        root = self.tree.init_root("夸父", metadata={"v": "0.4"})
        assert root.name == "夸父"
        assert self.tree.resolve("/") is root

    def test_spawn_child(self):
        self.tree.init_root("root")
        child = self.tree.spawn("调研", AgentPath.root(),
                                metadata={"task": "research"})
        assert child is not None
        assert child.name == "调研"
        assert not child.path.is_root()
        assert self.tree.resolve("/调研") is child

    def test_spawn_with_parent_path(self):
        self.tree.init_root("root")
        child = self.tree.spawn("调研", AgentPath.root())
        assert child is not None

        grandchild = self.tree.spawn("搜索", child.path)
        assert grandchild is not None
        assert self.tree.resolve(str(grandchild.path)) is grandchild

    def test_spawn_nonexistent_parent(self):
        self.tree.init_root("root")
        child = self.tree.spawn("orphan", AgentPath.parse("/nonexistent"))
        assert child is None

    def test_list_children(self):
        self.tree.init_root("root")
        self.tree.spawn("a", AgentPath.root())
        self.tree.spawn("b", AgentPath.root())
        self.tree.spawn("c", AgentPath.root())
        children = self.tree.list_children("/")
        assert len(children) == 3

    def test_get_tree_diagram(self):
        """树形图包含所有 agent"""
        self.tree.init_root("夸父")
        child = self.tree.spawn("调研", AgentPath.root())
        self.tree.spawn("搜索", child.path)
        diagram = self.tree.get_tree()
        assert "夸父" in diagram
        assert "调研" in diagram

    def test_get_stats(self):
        self.tree.init_root("root")
        stats = self.tree.get_stats()
        assert stats["total"] == 1
