# -*- coding: utf-8 -*-
"""
配置管理工具测试 —— coral_stats / coral_config_get / coral_config_set。

覆盖：
1. config_get：全量 / 点分路径 / 嵌套 / 不存在路径报错
2. config_set：修改生效 + 持久化到配置文件 + 重启后保持
3. 受保护路径：paths.* / embedding.* / threads.* 拒绝修改
4. 改 capacity_threshold 立即触发治理
5. coral_stats 返回 stats + disk_usage

运行：python tests/test_config_tools.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from three_dog_coral import ThreeDogCoral  # noqa: E402

PASS = 0
_TMP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp_cfg")


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        raise AssertionError(f"{name}: {detail}")
    PASS += 1


def setup(tmp: str, capacity: int = 1000) -> str:
    """建一个临时 coral_config.json（hash 嵌入，快）。返回配置路径。"""
    os.makedirs(tmp, exist_ok=True)
    cfg_path = os.path.join(tmp, "coral_config.json")
    data = {
        "paths": {
            "warm_cache": os.path.join(tmp, "coral_warm.json"),
            "cold_archive": os.path.join(tmp, "coral_cold.jsonl"),
            "vector_store": os.path.join(tmp, "coral_vectors.npy"),
            "vector_index": os.path.join(tmp, "coral_vector_index.json"),
        },
        "threads": {"path": os.path.join(tmp, "coral_threads.json")},
        "embedding": {"embedder": "hash", "dim": 128, "normalize": True},
        "memory": {"capacity_threshold": capacity, "governance_headroom": 2},
        "parallelism": {"embed_batch_window_ms": 0},
        "storage": {"max_bytes": 0},
    }
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return cfg_path


async def main() -> None:
    tmp = os.path.join(_TMP, "cfg_test")
    cfg_path = setup(tmp)

    print("\n[1] config_get")
    coral = ThreeDogCoral(config_path=cfg_path)
    full = await coral.config_get()
    ok("config_get 全量是 dict 且含 memory", isinstance(full, dict) and "memory" in full)
    cap = await coral.config_get("memory.capacity_threshold")
    ok("config_get 点分路径", cap == 1000, f"cap={cap}")
    w = await coral.config_get("retrieval.weights.vector")
    ok("config_get 嵌套路径", w == 0.6, f"vector={w}")
    try:
        await coral.config_get("memory.no_such_key")
        ok("config_get 不存在路径报错", False)
    except KeyError:
        ok("config_get 不存在路径报错", True)

    print("\n[2] config_set 生效 + 持久化")
    r = await coral.config_set("memory.capacity_threshold", 2000)
    ok("config_set 返回 old/new", r["old"] == 1000 and r["new"] == 2000, str(r))
    ok("config_set 内存即时生效", (await coral.config_get("memory.capacity_threshold")) == 2000)
    on_disk = json.load(open(cfg_path, encoding="utf-8"))
    ok("config_set 已写回配置文件", on_disk["memory"]["capacity_threshold"] == 2000)
    coral2 = ThreeDogCoral(config_path=cfg_path)
    ok("重启后保持修改", (await coral2.config_get("memory.capacity_threshold")) == 2000)

    print("\n[3] 受保护路径")
    for bad in ("paths.warm_cache", "embedding.model_name", "embedding.dim", "threads.path"):
        try:
            await coral.config_set(bad, "x")
            ok(f"config_set 拒绝 {bad}", False)
        except ValueError:
            ok(f"config_set 拒绝 {bad}", True)
    try:
        await coral.config_set("no.such.path", 1)
        ok("config_set 不存在路径报错", False)
    except KeyError:
        ok("config_set 不存在路径报错", True)

    print("\n[4] 改容量立即治理")
    await coral.insert("记忆 A 独特内容 xyz123")
    await coral.insert("记忆 B 独特内容 abc456")
    await coral.insert("记忆 C 独特内容 def789")
    before = coral.stats()["total"]
    ok("插入 3 条后 total=3", before == 3, f"total={before}")
    # 触发线 = capacity(1) + headroom(2) = 3，插到 4 条必然触发治理并收敛到 <=3
    await coral.config_set("memory.capacity_threshold", 1)
    await coral.insert("记忆 D 独特内容 ghi000")
    after = coral.stats()["total"]
    ok("调小容量立即治理收敛", after <= 3, f"total={after}")

    print("\n[5] coral_stats 工具")
    from three_dog_coral import coral_stats as stats_tool
    sr = await stats_tool()
    ok("coral_stats 含 stats 与 disk_usage",
       sr["ok"] and "stats" in sr and "disk_usage" in sr, str(sr.get("stats")))

    print(f"\n全部通过：{PASS} 项断言 ✅")


if __name__ == "__main__":
    asyncio.run(main())
