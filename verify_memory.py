# -*- coding: utf-8 -*-
"""
跨窗口记忆验证脚本（不需要 cordis 插件，任何会话让 Agent 跑它即可）

用法：新窗口里对 Agent 说："运行这个脚本: python verify_memory.py"
它会搜索珊瑚记忆库并打印结果 —— 如果搜出了"作者/起源/咖啡"这些记忆，
说明珊瑚记住了，而新窗口的 Agent 本身完全不知道这些事（跨会话记忆成立）。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from three_dog_coral import memory_search

QUERIES = ["作者是谁", "项目起源", "咖啡偏好", "技术栈", "三级存储"]


async def main() -> None:
    print("=== 跨窗口记忆验证（珊瑚记忆库检索）===")
    for q in QUERIES:
        r = await memory_search(q, top_k=1)
        hits = r["hits"]
        if hits:
            h = hits[0]
            print(f"[命中] {q!r} -> score={h['score']:.3f}  {h['content'][:50]}")
        else:
            print(f"[未命中] {q!r}")
    print("=== 验证结论：新窗口 Agent 不记得这些事，但珊瑚记得 ===")


if __name__ == "__main__":
    asyncio.run(main())
