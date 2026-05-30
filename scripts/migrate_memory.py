"""
迁移旧 JSON 记忆文件到 SQLite facts 表。
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.memory import MemoryManager

mm = MemoryManager()

count = 0

# 1. user_prefs.json
prefs_path = ROOT / "memory" / "user_prefs.json"
if prefs_path.exists():
    try:
        prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
        for k, v in prefs.items():
            if isinstance(v, str) and len(v.strip()) > 5:
                mm.store_fact(v.strip(), category="preference")
                count += 1
        print(f"[migrate] user_prefs: {count} 条")
    except Exception as e:
        print(f"[migrate] user_prefs 失败: {e}")

# 2. reflections.json
ref_path = ROOT / "memory" / "reflections.json"
ref_count = 0
if ref_path.exists():
    try:
        refs = json.loads(ref_path.read_text(encoding="utf-8"))
        if isinstance(refs, list):
            for r in refs:
                if isinstance(r, dict):
                    content = r.get("content", r.get("CONTENT", ""))
                    if content and len(str(content)) > 10:
                        mm.store_lesson(str(content)[:500])
                        ref_count += 1
        print(f"[migrate] reflections: {ref_count} 条")
    except Exception as e:
        print(f"[migrate] reflections 失败: {e}")

# 统计
stats = mm.get_stats()
print(f"\n迁移完成:")
print(f"  facts 表: {stats['facts_count']} 条")
print(f"  印象: {stats['longterm']['valid']} 条")
print(f"  缓存: {stats['cache_count']} 条")
