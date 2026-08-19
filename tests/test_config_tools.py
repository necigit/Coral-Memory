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
import shutil
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
    shutil.rmtree(_TMP, ignore_errors=True)   # 清上次运行的残留（含迁移目标 newdir），保证可重复运行
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

    print("\n[6] coral_report 审计报告")
    from three_dog_coral import coral_report as report_tool
    rr = await report_tool()
    ok("report 含 stats/disk/直方图/预警/路径",
       rr["ok"] and all(k in rr for k in ("stats", "disk_usage", "day_histogram", "eviction_preview", "paths")))
    ok("直方图总量与 stats.total 一致",
       rr["day_histogram"]["total"] == rr["stats"]["total"],
       f"hist={rr['day_histogram']['total']} stats={rr['stats']['total']}")
    ok("淘汰预警最多 5 条且带热度/位置",
       len(rr["eviction_preview"]) <= 5 and all("heat" in x and "location" in x for x in rr["eviction_preview"]))
    ok("路径是绝对路径", os.path.isabs(rr["paths"]["warm"]) and os.path.isabs(rr["paths"]["threads"]))
    ok("近 14 天键齐全", len(rr["day_histogram"]["last_14_days"]) == 14)

    print("\n[7] coral_config_reset 恢复默认（不清缓存）")
    await coral.config_set("memory.capacity_threshold", 2000)
    await coral.config_set("retrieval.top_k", 9)
    mem_files_before = {p: os.path.exists(p) for p in (coral.path_warm, coral.path_cold)}
    r = await coral.config_reset()
    ok("全量重置: 容量回默认", (await coral.config_get("memory.capacity_threshold")) == 1000)
    ok("全量重置: top_k 回默认", (await coral.config_get("retrieval.top_k")) == 3)
    ok("重置后写回文件", json.load(open(cfg_path, encoding="utf-8"))["memory"]["capacity_threshold"] == 1000)
    mem_files_after = {p: os.path.exists(p) for p in (coral.path_warm, coral.path_cold)}
    ok("缓存文件原样保留（前后一致，不增不减）", mem_files_before == mem_files_after, str(mem_files_before))
    ok("paths/embedding 未被重置",
       json.load(open(cfg_path, encoding="utf-8"))["paths"]["warm_cache"].endswith("coral_warm.json"))
    # 单键重置
    await coral.config_set("heat.freq_scale", 3.0)
    r2 = await coral.config_reset("heat.freq_scale")
    ok("单键重置", (await coral.config_get("heat.freq_scale")) == 10.0 and r2["scope"] == "heat.freq_scale")
    # 受保护路径
    for bad in ("paths.warm_cache", "embedding.dim", "threads.path"):
        try:
            await coral.config_reset(bad)
            ok(f"reset 拒绝 {bad}", False)
        except ValueError:
            ok(f"reset 拒绝 {bad}", True)

    print("\n[8] set_paths 用户自定义缓存路径（迁移）")
    import three_dog_coral as m
    pcfg = json.loads(json.dumps(__import__("three_dog_coral").DEFAULT_CONFIG))
    pcfg["embedding"] = {"embedder": "hash", "dim": 128, "normalize": True}
    pcfg["parallelism"]["embed_batch_window_ms"] = 0
    pcfg["memory"]["capacity_threshold"] = 1000
    # 关键：路径必须指向临时目录！绝不能落在真实 memory_data/（否则会毁掉真实缓存）
    pcfg["paths"] = {k: os.path.join(tmp, "p8", v.split("/")[-1]) for k, v in pcfg["paths"].items()}
    pcfg["threads"] = {"path": os.path.join(tmp, "p8", "coral_threads.json")}
    pcfg_path = os.path.join(tmp, "coral_config2.json")
    with open(pcfg_path, "w", encoding="utf-8") as f:
        json.dump(pcfg, f, ensure_ascii=False)
    pc = m.ThreeDogCoral(config_path=pcfg_path)
    await pc.insert("迁移记忆 X")
    await pc.flush()
    old_warm = pc.path_warm
    old_vec = pc.path_vectors
    ok("迁移前旧文件存在", os.path.exists(old_warm) and os.path.exists(old_vec))
    rp = await pc.set_paths({
        "warm_cache": "newdir/coral_warm.json",
        "cold_archive": "newdir/coral_cold.jsonl",
        "vector_store": "newdir/coral_vectors.npy",
        "vector_index": "newdir/coral_vector_index.json",
        "threads": "newdir/coral_threads.json",
    })
    ok("set_paths 返回 moved 与 restart_required", len(rp["moved"]) >= 4 and rp["restart_required"])
    newdir = os.path.join(tmp, "newdir")
    ok("旧文件已搬走", not os.path.exists(old_warm) and not os.path.exists(old_vec))
    ok("新文件就位", all(os.path.exists(os.path.join(newdir, n)) for n in
       ("coral_warm.json", "coral_vectors.npy", "coral_vector_index.json", "coral_threads.json")))
    on_disk2 = json.load(open(pcfg_path, encoding="utf-8"))
    ok("配置已指向新路径", on_disk2["paths"]["warm_cache"].startswith(newdir) and on_disk2["threads"]["path"].startswith(newdir))
    # 非法键 / 覆盖已存在文件
    try:
        await pc.set_paths({"bogus": "x"})
        ok("set_paths 拒绝未知键", False)
    except ValueError:
        ok("set_paths 拒绝未知键", True)
    await pc.set_paths({"warm_cache": "newdir/coral_warm.json"})  # 幂等：同路径跳过
    ok("同路径幂等（不报错不移动）", True)

    print(f"\n全部通过：{PASS} 项断言 (OK)")


if __name__ == "__main__":
    asyncio.run(main())
