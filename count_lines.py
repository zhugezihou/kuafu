#!/usr/bin/env python3
"""
统计当前目录下所有 .py 文件的总行数（含空行和注释）
"""

import os
import sys

def count_lines_in_py_files(directory="."):
    total_lines = 0
    file_details = []

    for entry in os.listdir(directory):
        if entry.endswith(".py") and os.path.isfile(os.path.join(directory, entry)):
            path = os.path.join(directory, entry)
            try:
                with open(path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
                    line_count = len(lines)
                    total_lines += line_count
                    file_details.append((entry, line_count))
                    print(f"  {entry:40s} {line_count:>6d} 行")
            except Exception as e:
                print(f"  {entry:40s} 读取失败: {e}", file=sys.stderr)

    print("-" * 50)
    print(f"  {'总计':40s} {total_lines:>6d} 行")
    print(f"  Python 文件数: {len(file_details)}")

    return total_lines

if __name__ == "__main__":
    count_lines_in_py_files()
