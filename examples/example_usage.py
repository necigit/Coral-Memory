# -*- coding: utf-8 -*-
"""
ThreeDogCoral 使用示例
======================

覆盖：插入（向量生成）、多路融合检索、重要性标记、配置热重载、
Agent 工具注册、DSH 桥接（cordis 插件 JS + Sidecar）、容量治理
（蒸馏占位 + 热度淘汰）。

注意：示例全程使用 get_coral() 单例，与 @register_tool 注册的
memory_search / memory_insert 工具共享同一份记忆，Agent 调工具时
能看到示例里插入的内容。

运行：python example_usage.py
"""

import asyncio
import json
import os
import sys
import urllib.request

# ---- 仓库根目录引导（子目录直接运行时也能 import 根模块） ----
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from three_dog_coral import (
    build_dsh_cordis_plugin_js,
    export_dsh_tool_definitions,
    get_coral,
    memory_insert,
    memory_search,
    MemoryToolSidecar,
)

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "coral_config.json")

# 一批中文/中英混合记忆，用来观察 CJK 分词与融合检索
SEED_MEMORIES = [
    "用户喜欢喝拿铁咖啡，尤其是冰镇的",
    "三狗珊瑚项目采用多路融合检索：向量 + 关键词 + 时间衰减",
    "后端服务部署在 127.0.0.1:3080，是 DeepSeek Harness 的 Web 界面",
    "记忆系统有三级存储：热区、温区、冷区",
    "模型 all-MiniLM-L6-v2 输出 384 维向量",
    "用户的开发环境是 Windows + Python 3.13",
    "公司茶水间的咖啡机每天早上九点前要预热",
    "向量检索权重 0.6，关键词 Jaccard 权重 0.2，时间衰减权重 0.2",
    "淘汰策略：先蒸馏相似记忆，再淘汰热度最低的记忆",
    "项目代号三狗珊瑚，英文名 ThreeDogCoral",
    "DeepSeek Harness 支持 cordis 插件动态注册工具",
    "温存数据持久化为 JSON，冷存数据为 JSONL",
]


async def main() -> None:
    # ---------------------------------------------------------------
    # 1. 初始化（单例，Agent 工具与之共享）
    # ---------------------------------------------------------------
    coral = get_coral(CONFIG_PATH)
    print("=" * 60)
    print("1) 插入记忆（自动生成 384 维向量 -> .npy 并行存储）")
    for text in SEED_MEMORIES:
        item = await coral.insert(text)
        print(f"   [{item.item_id if item else 'dup'}] {text[:28]}...")

    # ---------------------------------------------------------------
    # 2. 多路融合检索：Top-5 带综合得分
    # ---------------------------------------------------------------
    print("\n" + "=" * 60)
    print("2) 检索：'咖啡机什么时候预热？'（向量 0.6 + Jaccard 0.2 + 时间衰减 0.2）")
    hits = await coral.search("咖啡机什么时候预热？")
    for rank, hit in enumerate(hits, 1):
        print(f"   #{rank} score={hit.score:.4f} "
              f"(vec={hit.scores['vector']:.3f}, jac={hit.scores['jaccard']:.3f}, "
              f"time={hit.scores['time']:.3f})  {hit.content[:36]}")

    print("\n   检索：'记忆淘汰策略是什么？'")
    for rank, hit in enumerate(await coral.search("记忆淘汰策略是什么？"), 1):
        print(f"   #{rank} score={hit.score:.4f}  {hit.content[:36]}")

    # ---------------------------------------------------------------
    # 3. 用户显式标记重要性（热度权重 0.3）
    # ---------------------------------------------------------------
    print("\n" + "=" * 60)
    print("3) 标记重要性：把'向量检索权重…'那条标为 1.0（高热度保护）")
    target = next(h for h in await coral.search("向量检索权重") if h.scores["jaccard"] > 0.3)
    ok = await coral.mark_important(target.item_id, importance=1.0)
    print(f"   mark_important -> {ok} (id={target.item_id}, importance=1.0)")

    # ---------------------------------------------------------------
    # 4. 配置热加载：改 top_k 立即生效，无需重启
    # ---------------------------------------------------------------
    print("\n" + "=" * 60)
    print("4) 配置热加载：把 top_k 临时改成 3（写入 coral_config.json 再 reload）")
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["retrieval"]["top_k"] = 3
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    coral.reload_config(force=True)  # 生产环境可省略：操作时会自动检测 mtime
    hits3 = await coral.search("咖啡")
    print(f"   reload 后 top_k={coral.cfg['retrieval']['top_k']}，返回 {len(hits3)} 条")

    # 恢复原配置
    cfg["retrieval"]["top_k"] = 5
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    coral.reload_config(force=True)

    # ---------------------------------------------------------------
    # 5. Agent 工具：@register_tool 注册的 memory_search / memory_insert
    # ---------------------------------------------------------------
    print("\n" + "=" * 60)
    print("5) Harness 工具注册：memory_search / memory_insert（与示例共享同一份记忆）")
    result = await memory_search("部署在哪个端口？")
    print(f"   memory_search -> {json.dumps(result, ensure_ascii=False)[:220]}")
    result = await memory_insert("用户提醒：周五下午三点开周会", importance=0.8)
    print(f"   memory_insert -> {result}")

    defs = export_dsh_tool_definitions()
    print(f"   export_dsh_tool_definitions() -> {[d['name'] for d in defs]}")

    # ---------------------------------------------------------------
    # 6. DSH 桥接：生成 cordis 插件 JS + 启动 Sidecar 验证
    # ---------------------------------------------------------------
    print("\n" + "=" * 60)
    print("6) DSH 桥接：cordis 插件 JS + MemoryToolSidecar")
    plugin_js = build_dsh_cordis_plugin_js()
    print(f"   插件 JS 前 300 字符：\n{plugin_js[:300]}...")

    sidecar = MemoryToolSidecar(port=8765)
    sidecar.start()
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8765/rpc",
            data=json.dumps({"tool": "memory_search", "args": {"query": "周会", "top_k": 2}}).encode(),
            headers={"content-type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            print(f"   POST /rpc memory_search -> {resp.read().decode()[:220]}")
    finally:
        sidecar.stop()
        print("   sidecar 已停止")

    # ---------------------------------------------------------------
    # 7. 容量治理：先蒸馏（占位），再淘汰最低热度
    # ---------------------------------------------------------------
    print("\n" + "=" * 60)
    print("7) 容量治理：把容量阈值压到 8，插入 30 条噪音记忆触发蒸馏/淘汰")
    # 临时把查重阈值拉高，避免噪音条目互相合并（它们共享大量词汇）
    saved_threshold = coral.cfg["memory"]["sim_threshold_hot"]
    coral.cfg["memory"]["sim_threshold_hot"] = 0.95
    coral.cfg["memory"]["capacity_threshold"] = 8
    for i in range(30):
        await coral.insert(f"噪音测试记忆第 {i} 号：今天天气不错，适合散步和喝{i}杯咖啡")
    coral.cfg["memory"]["sim_threshold_hot"] = saved_threshold
    stats = coral.stats()
    print(f"   stats: {stats}")
    print("   （_distill 为占位接口返回 None -> 跳过蒸馏，直接淘汰热度最低的记忆）")

    # ---------------------------------------------------------------
    # 8. 落盘产物检查
    # ---------------------------------------------------------------
    print("\n" + "=" * 60)
    print("8) 落盘产物：")
    for path in (
        coral.path_warm,
        coral.path_cold,
        coral.path_vectors,
        coral.path_vector_index,
    ):
        exists = os.path.exists(path)
        size = os.path.getsize(path) if exists else 0
        print(f"   {os.path.relpath(path):34s} exists={exists}  size={size}")

    await coral.flush()
    print("\n完成 ✅  三狗珊瑚已就位：动态、可生长、可热加载。")


def _quiet_noisy_loggers() -> None:
    for name in ("httpx", "huggingface_hub", "sentence_transformers", "urllib3"):
        import logging

        logging.getLogger(name).setLevel(logging.WARNING)


if __name__ == "__main__":
    import logging
    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 终端中文不乱码
    except Exception:  # noqa: BLE001
        pass
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    _quiet_noisy_loggers()
    asyncio.run(main())
