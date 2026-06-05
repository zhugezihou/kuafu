"""pytest conftest: 自定义文件收集规则。"""
import pytest


def pytest_collect_file(parent, file_path):
    """跳过 test_all.py（自定义测试框架，非 pytest 测试）。"""
    if file_path.name == "test_all.py":
        return None
