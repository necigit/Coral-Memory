# -*- coding: utf-8 -*-
"""
容量(硬盘) ↔ 命中率/上下文 权衡曲线
=====================================

验证"用硬盘空间换命中率/上下文"的优化方向：
  - 同一份语料（3 项目 × 100 条 + 8 条共享偏好，共 308 条）
  - 采用上一轮验证的最优形态：共享池 + 项目亲缘度重排
  - 扫描容量阈值 capacity ∈ {50, 100, 200, 400, 无限}
  - 指标：total 留存、recall@5、precision@5、磁盘占用(B)、平均检索耗时(ms)

运行：python bench_disk_tradeoff.py
"""

import asyncio
import json
import os
import random
import shutil
import sys
import tempfile
import time

# ---- 仓库根目录引导（子目录直接运行时也能 import 根模块） ----
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from three_dog_coral import ThreeDogCoral

AFFINITY_BOOST = 0.15
PROJECTS = ("A", "B", "C")
FRAMES = {
    "A": ("技术文档", ["接口", "连接池", "超时", "缓存", "令牌", "部署", "日志", "压测"]),
    "B": ("产品说明", ["说明书", "按钮", "指示灯", "保修", "配对", "蓝牙", "锂电池", "参数"]),
    "C": ("美食推荐", ["招牌", "麻辣", "甜品", "火锅", "烤串", "寿司", "奶茶", "早茶"]),
}
SHARED_PREFS = [
    "S:用户偏好：技术类文档翻译风格要正式",
    "S:用户偏好：术语保留英文原文加中文注释",
    "S:用户偏好：法律与说明书类内容必须直译",
    "S:用户偏好：营销类文案可以意译突出卖点",
    "S:用户偏好：翻译时用'您'称呼客户",
    "S:用户偏好：代码注释要尽量简短",
    "S:用户偏好：标题一律用名词短语",
    "S:用户偏好：产品说明要用温柔亲切的口吻",
]


def build_corpus(seed: int = 7):
    rng = random.Random(seed)
    mem: dict = {}
    for pj, (domain, words) in FRAMES.items():
        mem[pj] = [
            f"{pj}:{domain}第 {i} 条：{rng.choice(words)} 的 {rng.randint(10, 99)} 号细节，参数 {rng.randint(1, 999)}"
            for i in range(100)
        ]
    queries: dict = {}
    for pj, (domain, words) in FRAMES.items():
        queries[pj] = [
            f"请把{domain}翻译成英文：{rng.choice(words)} 相关的 {rng.randint(10, 99)} 号细节怎么说"
            for _ in range(12)
        ]
    return mem, queries


def build_coral(tmp: str, capacity: int) -> ThreeDogCoral:
    cfg = {
        "paths": {
            "warm_cache": os.path.join(tmp, "warm.json"),
            "cold_archive": os.path.join(tmp, "cold.jsonl"),
            "vector_store": os.path.join(tmp, "vec.npy"),
            "vector_index": os.path.join(tmp, "vec_idx.json"),
        },
        "embedding": {"embedder": "hash", "dim": 384},
        "memory": {
            "sim_threshold_hot": 0.95,
            "hot_ttl_hours": 1,
            "max_hot_entries": 50,
            "max_warm_entries": 100,
            "max_cold_entries": 20000,
            "capacity_threshold": capacity,
            "cold_scan_lines": 20000,
        },
        "retrieval": {"weights": {"vector": 0.6, "jaccard": 0.2, "time": 0.2},
                      "top_k": 5, "tau_days": 7.0, "include_cold": True},
        "reload": {"check_interval_seconds": 60},
    }
    os.makedirs(tmp, exist_ok=True)
    p = os.path.join(tmp, "coral_config.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)
    return ThreeDogCoral(p)


def is_relevant(content: str, project: str) -> bool:
    return content.startswith((f"{project}:", "S:"))


def disk_bytes(coral: ThreeDogCoral) -> int:
    # 用投影值（向量未落盘也算），与磁盘配额语义一致
    return coral.disk_usage()["total"]


async def run_capacity(tmp_root: str, tag: str, capacity: int, mem: dict, queries: dict) -> dict:
    coral = build_coral(os.path.join(tmp_root, f"cap-{tag}"), capacity)
    for pj in PROJECTS:
        for m in mem[pj]:
            await coral.insert(m)
    for m in SHARED_PREFS:
        await coral.insert(m)

    recall = precision = 0.0
    latencies: list = []
    n = sum(len(queries[pj]) for pj in PROJECTS)
    for pj in PROJECTS:
        for q in queries[pj]:
            t0 = time.perf_counter()
            hits = await coral.search(q, top_k=50)  # 亲缘度重排需要足够候选
            latencies.append((time.perf_counter() - t0) * 1000)
            ranked = sorted(
                hits,
                key=lambda h: h.score + (AFFINITY_BOOST if is_relevant(h.content, pj) else 0.0),
                reverse=True,
            )[:5]
            rel = [h for h in ranked if is_relevant(h.content, pj)]
            if rel:
                recall += 1
            precision += len(rel) / 5.0

    stats = coral.stats()
    return {
        "capacity": capacity,
        "total": stats["total"],
        "recall@5": round(recall / n, 3),
        "precision@5": round(precision / n, 3),
        "disk_bytes": disk_bytes(coral),
        "avg_ms": round(sum(latencies) / len(latencies), 2),
    }


async def main() -> None:
    tmp = tempfile.mkdtemp(prefix="coral-disk-")
    try:
        print("=" * 84)
        print("容量(硬盘) ↔ 命中率/上下文 权衡曲线（共享池 + 亲缘度重排，308 条记忆，3 seed 平均）")
        print("=" * 84)
        print(f"{'capacity':<10}{'留存':<8}{'recall@5':<10}{'precision@5':<12}{'磁盘占用':<14}{'平均检索ms':<10}")
        seeds = (7, 13, 29)
        for cap in (50, 100, 200, 400, 10 ** 9):  # 10**9 = 不淘汰
            acc = {"total": 0, "recall": 0.0, "precision": 0.0, "disk": 0, "ms": 0.0}
            for seed in seeds:
                mem, queries = build_corpus(seed)
                r = await run_capacity(tmp, f"{cap}-{seed}", cap, mem, queries)
                acc["total"] += r["total"]
                acc["recall"] += r["recall@5"]
                acc["precision"] += r["precision@5"]
                acc["disk"] += r["disk_bytes"]
                acc["ms"] += r["avg_ms"]
            k = len(seeds)
            cap_label = "无限" if cap == 10 ** 9 else str(cap)
            print(f"{cap_label:<10}{acc['total'] // k:<8}"
                  f"{acc['recall'] / k:<10.3f}{acc['precision'] / k:<12.3f}"
                  f"{acc['disk'] // k:<14}{acc['ms'] / k:<10.2f}")
        print("-" * 84)
        print("注：磁盘占用 = warm.json + cold.jsonl + vec.npy + 索引；每条记忆约 1.6KB（文本+向量）。")
        print("    context 旋钮 = top_k：top_k=20 时每次检索回传约 20×~200B 文本上下文。")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    asyncio.run(main())
