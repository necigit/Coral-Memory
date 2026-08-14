# -*- coding: utf-8 -*-
"""
并发/并行基准：2020 年前后消费级 CPU（6C/12T ~ 8C/16T）假设
=============================================================

四个实测环节：
  A. 嵌入批量缩放：真实 all-MiniLM-L6-v2，CPU 推理，torch 线程 1/4/8，
     batch 1/8/64 -> 证明"合批"是嵌入并行的主杠杆，线程数次之
  B. Jaccard 打分：旧"逐对重新分词" vs 新"numpy 位图向量化"
     （GIL 下 Python 循环加线程没用，向量化才是出路）
  C. 端到端检索延迟：2000 条池，vectorized_jaccard on/off
  D. 并发工具调用：真实模型 + 8 路并发 search，
     合批窗口 0ms vs 8ms（体现"嵌入放锁外 + 合批"的收益）

运行：python bench_parallelism.py
"""

import asyncio
import json
import os
import random
import shutil
import sys
import tempfile
import time

import numpy as np

# ---- 仓库根目录引导（子目录直接运行时也能 import 根模块） ----
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from three_dog_coral import ThreeDogCoral, _token_bitmap, _jaccard_many, _hash_tokens, _jaccard_similarity

POOL_SIZE = 2000


def build_coral(tmp: str, embedder: str = "hash") -> ThreeDogCoral:
    cfg = {
        "paths": {
            "warm_cache": os.path.join(tmp, "warm.json"),
            "cold_archive": os.path.join(tmp, "cold.jsonl"),
            "vector_store": os.path.join(tmp, "vec.npy"),
            "vector_index": os.path.join(tmp, "vec_idx.json"),
        },
        "embedding": {"embedder": embedder, "dim": 384},
        "memory": {
            "sim_threshold_hot": 0.95, "hot_ttl_hours": 1,
            "max_hot_entries": 500, "max_warm_entries": 500,
            "max_cold_entries": 100000, "capacity_threshold": 100000,
        },
        "retrieval": {"weights": {"vector": 0.6, "jaccard": 0.2, "time": 0.2},
                      "top_k": 5, "tau_days": 7.0, "include_cold": True,
                      "vectorized_jaccard": True},
        "parallelism": {"embed_batch_window_ms": 0, "vectorized_jaccard": True},
        "reload": {"check_interval_seconds": 60},
    }
    os.makedirs(tmp, exist_ok=True)
    p = os.path.join(tmp, "coral_config.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)
    return ThreeDogCoral(p)


def gen_pool_texts(n: int, seed: int = 7) -> list:
    rng = random.Random(seed)
    words = ["接口", "缓存", "令牌", "部署", "日志", "压测", "连接池", "超时", "鉴权", "回滚",
             "说明书", "按钮", "指示灯", "保修", "配对", "蓝牙", "锂电池", "参数", "包装", "故障"]
    return [f"记忆 {i}：{rng.choice(words)}相关的第{rng.randint(1, 999)}号细节，参数{rng.randint(1, 999)}"
            for i in range(n)]


# ---------------- A. 嵌入批量缩放（真实模型 CPU） ----------------
def bench_embed_batch() -> None:
    import torch
    from sentence_transformers import SentenceTransformer

    print("[A] 嵌入批量缩放（all-MiniLM-L6-v2, device=cpu, 模拟消费级 CPU）")
    model = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2", device="cpu")
    texts = gen_pool_texts(256)
    for threads in (1, 4, 8):
        torch.set_num_threads(threads)
        row = []
        for batch in (1, 8, 64):
            sample = texts[:batch]
            model.encode(sample)  # 预热
            t0 = time.perf_counter()
            for _ in range(max(1, 64 // batch)):
                model.encode(sample, normalize_embeddings=True)
            dt = (time.perf_counter() - t0) / max(1, 64 // batch)
            row.append(f"batch={batch:<3} {dt * 1000:6.1f}ms ({dt / batch * 1000:5.2f}ms/条)")
        print(f"   torch线程={threads}: " + " | ".join(row))


# ---------------- B. Jaccard：旧 vs 新（稳态：索引建一次，比较每查询成本） ----------------
def bench_jaccard() -> None:
    print("\n[B] Jaccard 打分：旧(每查询全量重分词) vs 新(位图索引建一次 + 向量化)")
    rng = random.Random(7)
    for n in (2000, 20000):
        pool = gen_pool_texts(n)
        queries = [f"请把{rng.choice(['接口', '缓存', '说明书', '按钮'])}相关翻译成英文" for _ in range(20)]

        # 新：一次性建位图索引（缓存），之后每查询只有 stack + 向量化
        t0 = time.perf_counter()
        bitmap_idx = {m: _token_bitmap(_hash_tokens(m)) for m in pool}
        build_ms = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        for q in queries:
            q_bits = _token_bitmap(_hash_tokens(q))
            mat = np.stack([bitmap_idx[m] for m in pool])
            _jaccard_many(q_bits, mat)
        new_ms = (time.perf_counter() - t0) * 1000

        # 旧：每查询、每条目都重新分词 + set 运算
        t0 = time.perf_counter()
        for q in queries:
            for item in pool:
                _jaccard_similarity(set(_hash_tokens(q)), set(_hash_tokens(item)))
        old_ms = (time.perf_counter() - t0) * 1000
        print(f"   {n:>6} 条: 索引构建 {build_ms:7.1f}ms | "
              f"20 查询 -> 旧 {old_ms:8.1f}ms | 新 {new_ms:8.1f}ms | 稳态加速 {old_ms / max(new_ms, 0.001):6.1f}×")


# ---------------- C. 端到端检索延迟 ----------------
async def bench_e2e(tmp: str) -> None:
    print("\n[C] 端到端检索（2000 条池, hash 嵌入, 20 次查询）")
    coral = build_coral(os.path.join(tmp, "e2e"))
    for t in gen_pool_texts(POOL_SIZE):
        await coral.insert(t)
    queries = [f"请把接口超时相关的第{i}号细节翻译成英文" for i in range(20)]

    for vec_jac in (True, False):
        coral.cfg["retrieval"]["vectorized_jaccard"] = vec_jac
        coral._bitmap_cache.clear()
        await coral.search(queries[0])  # 预热（缓存建立）
        t0 = time.perf_counter()
        for q in queries:
            await coral.search(q)
        ms = (time.perf_counter() - t0) * 1000 / len(queries)
        print(f"   vectorized_jaccard={'on ' if vec_jac else 'off'}: 平均 {ms:6.2f} ms/查询")


# ---------------- D. 并发工具调用（真实模型 + 合批窗口） ----------------
async def bench_concurrent(tmp: str) -> None:
    print("\n[D] 8 路并发 memory_search（真实模型 CPU, 300 条池, torch 线程=6）")
    import torch
    torch.set_num_threads(6)
    coral = build_coral(os.path.join(tmp, "conc"), embedder="sentence-transformers")
    pool = gen_pool_texts(300)
    for t in pool:
        await coral.insert(t)
    queries = [f"请把{r}相关翻译成英文" for r in ("接口", "缓存", "令牌", "部署", "日志", "压测", "鉴权", "回滚")]

    for window in (0, 8):
        coral.cfg["parallelism"]["embed_batch_window_ms"] = window
        # 预热
        await coral.search(queries[0])

        t0 = time.perf_counter()
        await asyncio.gather(*(coral.search(q) for q in queries))
        parallel_ms = (time.perf_counter() - t0) * 1000

        t0 = time.perf_counter()
        for q in queries:
            await coral.search(q)
        serial_ms = (time.perf_counter() - t0) * 1000
        print(f"   窗口={window:>2}ms:  8 并发 {parallel_ms:7.1f}ms | 8 串行 {serial_ms:7.1f}ms "
              f"| 并发/串行 = {serial_ms / max(parallel_ms, 0.001):5.2f}×")


async def main() -> None:
    tmp = tempfile.mkdtemp(prefix="coral-parallel-")
    try:
        bench_embed_batch()
        bench_jaccard()
        await bench_e2e(tmp)
        await bench_concurrent(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    import logging

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    for _n in ("httpx", "huggingface_hub", "sentence_transformers"):
        logging.getLogger(_n).setLevel(logging.WARNING)
    asyncio.run(main())
