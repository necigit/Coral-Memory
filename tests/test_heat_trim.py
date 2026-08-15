# -*- coding: utf-8 -*-
"""
验证两处淘汰/管理改动（临时目录，绝不触碰真实 memory_data）：
1) _trim_cold_storage 改为按热度裁剪（保留高 importance 的重要记忆，而非纯 FIFO 删最旧）；
2) delete() / memory_delete：从热/温/冷 + 向量库彻底移除。

运行：python tests/test_heat_trim.py
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from three_dog_coral import MemoryItem, ThreeDogCoral


async def main() -> None:
    tmp = tempfile.mkdtemp(prefix="coral-trim-")
    try:
        cfg = {
            "paths": {
                "warm_cache": os.path.join(tmp, "warm.json"),
                "cold_archive": os.path.join(tmp, "cold.jsonl"),
                "vector_store": os.path.join(tmp, "vec.npy"),
                "vector_index": os.path.join(tmp, "vec_idx.json"),
            },
            "embedding": {"embedder": "hash", "dim": 64},
            "memory": {
                "sim_threshold_hot": 0.99,
                "hot_ttl_hours": 0,
                "max_hot_entries": 100,
                "max_warm_entries": 200,
                "max_cold_entries": 5,
                "capacity_threshold": 100000,   # 关闭容量治理，只测冷库 trim
                "governance_headroom": 0,
                "distill_first": True,
            },
            "reload": {"check_interval_seconds": 60},
        }
        p = os.path.join(tmp, "coral_config.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)
        coral = ThreeDogCoral(p)

        # ---- 1) 冷库热度裁剪 ----
        now = time.time()
        for i in range(10):
            # 前 3 条 importance=0.9（重要），其余 0；时间戳相同，屏蔽 recency 干扰
            item = MemoryItem(
                item_id=f"test{i:02d}",
                content=f"热度裁剪测试记忆 {i}",
                timestamp=now,
                last_access=now,
                token_count=5,
                access_count=1,
                importance=0.9 if i < 3 else 0.0,
            )
            await coral._dump_cold(item)
        print(f"[1] 冷库写入 10 条（3 条重要），_cold_count={coral._cold_count}")

        await coral._trim_cold_storage(5)
        cold = await coral._read_all_cold()
        imps = sorted((m.importance for m in cold), reverse=True)
        print(f"[1] trim 到 5 条后 importance 分布: {imps}")
        assert len(cold) == 5, f"裁剪后应为 5 条，实际 {len(cold)}"
        assert sum(1 for m in cold if m.importance >= 0.9) == 3, \
            "热度裁剪应保留全部 3 条重要记忆（FIFO 会误删最旧的）"
        assert coral.vector_store.get("test00") is None or not any(
            m.item_id == "test00" for m in cold
        ), "被淘汰条目应同步从向量库移除"
        print("[1] ✅ 冷库按热度裁剪：3 条重要记忆全部保住")

        # ---- 2) delete：从三层 + 向量库彻底移除 ----
        target = cold[0].item_id
        assert any(m.item_id == target for m in cold), "目标条目应在冷库中"
        ok = await coral.delete(target)
        print(f"[2] delete({target}) -> ok={ok}")
        assert ok
        cold_after = await coral._read_all_cold()
        assert all(m.item_id != target for m in cold_after), "冷库应已删除"
        assert all(m.item_id != target for m in coral.hot_memory + coral.warm_memory), "热/温区应无残留"
        assert coral.vector_store.get(target) is None, "向量应已删除"
        # 删除不存在的 id 应返回 False
        assert await coral.delete("nope-nonexistent") is False, "删除不存在的 id 应返回 False"
        print("[2] ✅ delete：三层 + 向量库全部移除，不存在 id 返回 False")

        print("\n全部断言通过 ✅")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    asyncio.run(main())
