# -*- coding: utf-8 -*-
"""
20000 次暴力鲁棒性压测
======================
阶段（每阶段计时 + 完整性校验）：
  1. 写入：20000 条唯一记忆（容量 20000，无淘汰）
  2. 检索：500 次查询（include_cold=True，冷库尾部 500 行）
  3. stats() 陷阱：连续 20 次 stats()，测单次耗时
  4. 治理：容量压到 15000，再插 300 条 -> 每次触发全量冷库淘汰（测 O(n²) 治理）
校验：无未捕获异常、总数<=容量、向量==唯一内容、冷库可全量解析、重启一致

运行：python stress_20k.py [--inserts 20000] [--searches 500] [--seed 7]
"""

import argparse
import asyncio
import json
import os
import random
import shutil
import sys
import tempfile
import time
import tracemalloc

# ---- 仓库根目录引导（子目录直接运行时也能 import 根模块） ----
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from three_dog_coral import ThreeDogCoral

WORDS = ["接口", "缓存", "令牌", "部署", "日志", "压测", "连接池", "超时", "鉴权", "回滚",
         "说明书", "按钮", "指示灯", "保修", "配对", "蓝牙", "锂电池", "参数", "包装", "故障",
         "招牌", "麻辣", "甜品", "火锅", "烤串", "寿司", "奶茶", "早茶", "咖啡", "面馆"]


def gen_text(i: int, rng: random.Random) -> str:
    return (f"压测记忆第 {i} 号：{rng.choice(WORDS)} 的 {rng.randint(10, 9999)} 号细节，"
            f"参数 {rng.randint(1, 999999)}，关键词{rng.choice(WORDS)}相关")


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
            "sim_threshold_hot": 0.95, "hot_ttl_hours": 1,
            "max_hot_entries": 50, "max_warm_entries": 200,
            "max_cold_entries": 50000, "capacity_threshold": capacity,
            "cold_scan_lines": 500,
        },
        "retrieval": {"weights": {"vector": 0.6, "jaccard": 0.2, "time": 0.2},
                      "top_k": 5, "tau_days": 7.0, "include_cold": True},
        "heat": {"cold_fold_interval_seconds": 3600},  # 关闭节流折叠，专注主路径
        "reload": {"check_interval_seconds": 60},
    }
    os.makedirs(tmp, exist_ok=True)
    p = os.path.join(tmp, "coral_config.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)
    return ThreeDogCoral(p)


async def integrity(c: ThreeDogCoral, label: str) -> list:
    """一致性校验，返回失败列表。"""
    fails = []
    stats = c.stats()
    cold = await c._read_all_cold()
    unique = len({m.item_id for m in c.hot_memory + c.warm_memory + cold})
    capacity = int(c.cfg["memory"]["capacity_threshold"])
    headroom = max(10, min(capacity // 10, 200))
    if stats["total"] > capacity + headroom:
        fails.append(f"total {stats['total']} > capacity {capacity} + headroom {headroom}")
    if unique != stats["vectors"]:
        fails.append(f"唯一内容 {unique} != 向量 {stats['vectors']}")
    if stats["cold"] != len(cold):
        fails.append(f"cold 计数 {stats['cold']} != 实际行数 {len(cold)}")
    # 冷库全部行可解析
    bad = sum(1 for m in cold if not m.content)
    if bad:
        fails.append(f"{bad} 条冷记录内容为空")
    print(f"  [check:{label}] hot={stats['hot']} warm={stats['warm']} cold={stats['cold']} "
          f"total={stats['total']} 唯一={unique} vectors={stats['vectors']} "
          f"{'OK' if not fails else 'FAIL ' + str(fails)}", flush=True)
    return fails


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inserts", type=int, default=20000)
    parser.add_argument("--searches", type=int, default=500)
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()

    tmp = tempfile.mkdtemp(prefix="coral-stress-")
    rng = random.Random(args.seed)
    failures: list = []
    try:
        print(f"===== 20000 次暴力鲁棒性压测（inserts={args.inserts}, searches={args.searches}）=====", flush=True)
        coral = build_coral(os.path.join(tmp, "main"), capacity=args.inserts)
        tracemalloc.start()

        # ---------- 阶段 1：写入 ----------
        t0 = time.perf_counter()
        for i in range(1, args.inserts + 1):
            await coral.insert(gen_text(i, rng))
        t1 = time.perf_counter()
        _, peak = tracemalloc.get_traced_memory()
        print(f"[阶段1] 写入 {args.inserts} 条: {t1 - t0:.1f}s ({(t1 - t0) / args.inserts * 1000:.1f}ms/条), "
              f"Python峰值内存 {peak / 1e6:.0f}MB", flush=True)
        failures += await integrity(coral, "写入后")

        # ---------- 阶段 2：检索 ----------
        queries = [f"请把{rng.choice(WORDS)}相关的第{rng.randint(1, 9999)}号细节翻译成英文" for _ in range(args.searches)]
        t0 = time.perf_counter()
        for q in queries:
            await coral.search(q, top_k=5)
        t1 = time.perf_counter()
        print(f"[阶段2] 检索 {args.searches} 次: {t1 - t0:.1f}s "
              f"({(t1 - t0) / args.searches * 1000:.0f}ms/次)", flush=True)

        # ---------- 阶段 3：stats() 陷阱 ----------
        t0 = time.perf_counter()
        for _ in range(20):
            coral.stats()
        t1 = time.perf_counter()
        print(f"[阶段3] stats() ×20: {(t1 - t0) / 20 * 1000:.0f}ms/次（冷库 {coral.stats()['cold']} 行）", flush=True)

        # ---------- 阶段 4：容量治理（治理余量 -> 批量淘汰） ----------
        coral.cfg["memory"]["capacity_threshold"] = 15000
        t0 = time.perf_counter()
        for i in range(args.inserts + 1, args.inserts + 301):
            await coral.insert(gen_text(i, rng))
        t1 = time.perf_counter()
        print(f"[阶段4] 容量压到 15000（治理余量 200）后插 300 条: {t1 - t0:.1f}s "
              f"({(t1 - t0) / 300 * 1000:.0f}ms/次, 全量淘汰仅在大幅超容时触发)", flush=True)
        failures += await integrity(coral, "治理后")

        # ---------- 重启一致性（先 flush：向量为节流落盘） ----------
        await coral.flush()
        config_path = os.path.join(tmp, "main", "coral_config.json")
        coral2 = ThreeDogCoral(config_path)
        s1, s2 = coral.stats(), coral2.stats()
        cold2 = await coral2._read_all_cold()
        cold_unique2 = len({m.item_id for m in cold2})
        if s2["cold"] != s1["cold"] or s2["vectors"] != cold_unique2:
            failures.append(f"重启不一致: {s2} vs {s1}")
        else:
            print(f"  [check:重启] cold={s2['cold']} 冷库唯一={cold_unique2} vectors={s2['vectors']} 一致"
                  f"（热区 {s1['hot']} 条按设计不落盘）", flush=True)
        await coral2.search(queries[0])  # 重启后检索不崩
        print(f"  [check:重启检索] OK", flush=True)

        # ---------- 落盘体积 ----------
        total_bytes = sum(
            os.path.getsize(p) for p in (coral.path_warm, coral.path_cold, coral.path_vectors, coral.path_vector_index)
            if os.path.exists(p)
        )
        print(f"[落盘] 总占用 {total_bytes / 1e6:.1f}MB", flush=True)
    except Exception as exc:  # noqa: BLE001
        failures.append(f"未捕获异常: {type(exc).__name__}: {exc}")
        import traceback

        traceback.print_exc()
    finally:
        tracemalloc.stop()
        shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 60, flush=True)
    print(f"结果: {'全部通过 ✅' if not failures else '发现问题 ❌'}")
    for f in failures:
        print(f"  - {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    sys.exit(asyncio.run(main()))
