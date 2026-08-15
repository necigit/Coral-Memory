# -*- coding: utf-8 -*-
"""
推理线索链路（Thread）压力测试 —— 大规模 + 多进程并发。

1. 大规模：300 条链路 × 20 次推进 = 6000 次操作，校验文件完整性/重启加载/耗时
2. 多进程并发：3 个进程同时推进同一条链路（各 30 次），校验"远端合并"不丢步骤
3. 混合状态：随机中断/恢复/归档，校验总览排序与过滤

运行：python stress/stress_threads.py
"""
import asyncio
import json
import os
import random
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from three_dog_coral import DEFAULT_CONFIG, ThreeDogCoral  # noqa: E402

WORKSPACE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = os.path.join(WORKSPACE, "stress", "_tmp_stress_threads")
CHILD_SCRIPT = os.path.join(WORKSPACE, "stress", "_thread_child.py")


def make_cfg(tmp: str) -> dict:
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    cfg["paths"] = {k: os.path.join(tmp, v.split("/")[-1]) for k, v in cfg["paths"].items()}
    cfg["threads"] = {"path": os.path.join(tmp, "coral_threads.json")}
    cfg["embedding"] = {"embedder": "hash", "dim": 128, "normalize": True}
    cfg["parallelism"]["embed_batch_window_ms"] = 0
    return cfg


async def stress_large(tmp: str) -> None:
    print("\n[1] 大规模：300 链路 × 20 推进 = 6000 操作")
    coral = ThreeDogCoral(cfg=make_cfg(tmp))
    t0 = time.perf_counter()

    created = []
    for i in range(300):
        parent = created[-1] if i and i % 10 == 0 else None
        t = await coral.thread_create(f"任务 #{i}", f"宏观路径 {i}: 分三步完成", parent_thread_id=parent, by="聊天A")
        created.append(t.thread_id)

    advances = 0
    for tid in created:
        for j in range(20):
            await coral.thread_advance(tid, f"推进 {j}: 检查点", done=(j % 5 == 4), by=f"聊天{chr(66 + j % 6)}")
            advances += 1

    # 混合状态（记录中断次数：只有 interrupt 会追加步骤节点）
    rng = random.Random(42)
    interrupt_count = 0
    for tid in created[:60]:
        r = rng.random()
        if r < 0.3:
            await coral.thread_interrupt(tid, reason="等外部依赖")
            interrupt_count += 1
        elif r < 0.5:
            await coral.thread_resume(tid)
        elif r < 0.7:
            await coral.thread_archive(tid)
    await coral.thread_resume(created[0])
    await coral.flush()   # 节流落盘契约：读盘前显式落盘

    elapsed = time.perf_counter() - t0
    st = coral.stats()
    file_size = os.path.getsize(coral.path_threads)
    print(f"  创建 300 + 推进 {advances} 次 + 状态变更 60 次，耗时 {elapsed:.2f}s "
          f"({advances/elapsed:.0f} 推进/秒)，文件 {file_size/1024:.0f}KB，stats.threads={st['threads']}")
    assert st["threads"] == 300, st

    # 完整性
    all_t = await coral.thread_status(include_archived=True)
    assert len(all_t) == 300
    total_steps = sum(len(t.steps) for t in all_t)
    assert total_steps == advances + interrupt_count, \
        f"步骤数 {total_steps} != 期望 {advances + interrupt_count}（推进 {advances} + 中断 {interrupt_count}）"
    print(f"  步骤总数校验: {total_steps} = 推进 {advances} + 中断 {interrupt_count} ✅")

    # 重启加载
    coral2 = ThreeDogCoral(cfg=make_cfg(tmp))
    all2 = await coral2.thread_status(include_archived=True)
    assert len(all2) == 300 and sum(len(t.steps) for t in all2) == total_steps
    assert {t.thread_id: t.status for t in all2} == {t.thread_id: t.status for t in all_t}
    print(f"  重启加载: 300 链路 / {total_steps} 步骤 / 状态一致 ✅")

    # 总览排序：active 在前
    ov = await coral2.thread_status()
    statuses = [t.status for t in ov]
    assert "archived" not in statuses, statuses
    first_active = next((i for i, s in enumerate(statuses) if s != "active"), len(statuses))
    assert all(s == "active" for s in statuses[:first_active]), statuses
    print(f"  活跃总览: {len(ov)} 条（归档已隐藏），active 优先排序 ✅")

    # 查询过滤耗时
    t1 = time.perf_counter()
    q = await coral2.thread_status(query="任务 #7")
    q_ms = (time.perf_counter() - t1) * 1000
    assert q, "query 应有命中"
    print(f"  关键词过滤: {len(q)} 条命中，{q_ms:.1f}ms ✅")

    # 线索链：i%10==0 的链路以 created[i-1] 为父，故 created[9] 的子链路含 created[10]
    t10 = (await coral2.thread_status(thread_id=created[10]))[0]
    assert t10.parent_thread_id == created[9], "created[10] 的父应是 created[9]"
    child_ids = [w.thread_id for w in await coral2.thread_status() if w.parent_thread_id == created[9]]
    assert created[10] in child_ids, "created[9] 应有子链路 created[10]"
    print(f"  线索链: 链路 9 有 {len(child_ids)} 条子链路（含 链路 10）✅")
    return total_steps


async def stress_concurrent(tmp: str) -> int:
    print("\n[2] 多进程并发：3 进程 × 30 次推进同一条链路（远端合并防丢步骤）")
    proj = os.path.join(TMP, "concurrent")
    os.makedirs(proj, exist_ok=True)
    cfg = make_cfg(proj)
    cfg_path = os.path.join(proj, "cfg.json")
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)

    coral = ThreeDogCoral(cfg=cfg)
    t = await coral.thread_create("并发协作链路", "三个聊天进程同时推进", by="主进程")
    tid = t.thread_id

    code = (
        "import asyncio, json, sys\n"
        "sys.path.insert(0, {ws!r})\n"
        "from three_dog_coral import ThreeDogCoral\n"
        "cfg = json.load(open({cfg!r}, encoding='utf-8'))\n"
        "c = ThreeDogCoral(cfg=cfg)\n"
        "async def go():\n"
        "    for i in range(30):\n"
        "        await c.thread_advance({tid!r}, '并发推进', by='子进程')\n"
        "    await c.flush()\n"
        "asyncio.run(go())\n"
    ).format(ws=WORKSPACE, cfg=cfg_path, tid=tid)

    t0 = time.perf_counter()
    procs = []
    for idx in range(3):
        errfile = os.path.join(proj, f"child_{idx}_err.txt")
        with open(errfile, "w", encoding="utf-8") as ef:
            procs.append((subprocess.Popen(
                [sys.executable, "-X", "utf8", "-c", code],
                cwd=WORKSPACE, stdout=subprocess.DEVNULL, stderr=ef,
            ), errfile))
    for p, errfile in procs:
        rc = p.wait(timeout=120)
        if rc != 0:
            try:
                detail = open(errfile, encoding="utf-8").read()[-800:]
            except OSError:
                detail = "(无 stderr 文件)"
            raise AssertionError(f"子进程退出码 {rc}\n--- stderr ---\n{detail}")
    elapsed = time.perf_counter() - t0

    final = (await coral.thread_status(thread_id=tid))[0]
    steps = len(final.steps)
    print(f"  3 进程并发推进 90 次，耗时 {elapsed:.2f}s，最终步骤数 = {steps}（期望 90）")
    # 本地实例需重新加载才能看到全部（其它进程写入）
    reloaded = ThreeDogCoral(cfg=cfg)
    final2 = (await reloaded.thread_status(thread_id=tid))[0]
    steps2 = len(final2.steps)
    print(f"  重启加载后步骤数 = {steps2}（期望 90）")
    assert steps2 == 90, f"丢步骤！只有 {steps2}/90"
    print("  并发推进无丢步骤 ✅")
    return steps2


async def main() -> None:
    shutil.rmtree(TMP, ignore_errors=True)   # 清掉上次运行的残留数据（保证可重复运行）
    os.makedirs(TMP, exist_ok=True)
    t0 = time.perf_counter()
    total1 = await stress_large(os.path.join(TMP, "large"))
    total2 = await stress_concurrent(TMP)
    elapsed = time.perf_counter() - t0
    print(f"\n线程压测全部通过 ✅  (大规模步骤 {total1} + 并发步骤 {total2}，总耗时 {elapsed:.2f}s)")


if __name__ == "__main__":
    asyncio.run(main())
