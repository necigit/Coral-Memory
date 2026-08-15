# -*- coding: utf-8 -*-
"""
推理线索链路（Thread）功能测试 —— 永不遗忘的跨聊天协作线索。

覆盖：
1. 创建/查看/推进/中断/恢复/归档/串联（thread_* 全流程）
2. 永不遗忘：不参与热度淘汰/容量治理（独立于热/温/冷池）
3. 持久化：重启加载不丢
4. 跨进程共享：多实例（模拟多聊天）通过 mtime 检测看到同一份链路
5. stats() 线程计数

运行：python tests/test_threads.py   （从仓库根目录）
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from three_dog_coral import DEFAULT_CONFIG, ThreeDogCoral  # noqa: E402

# 工作区内临时目录（沙箱只允许写工作区）
_TMP_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tmp_threads")


def _mkdtemp(prefix: str) -> str:
    """工作区内建临时目录。注意：不能用 tempfile.mkdtemp（0o700 目录会被
    文件沙箱按 POSIX 权限判定为不可写），用 os.makedirs 默认 0o777。"""
    os.makedirs(_TMP_ROOT, exist_ok=True)
    d = os.path.join(_TMP_ROOT, prefix + os.urandom(4).hex())
    os.makedirs(d)
    return d


PASS = 0


def ok(name: str, cond: bool, detail: str = "") -> None:
    global PASS
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        raise AssertionError(f"{name}: {detail}")
    PASS += 1


def make_cfg(tmp: str, capacity: int = 1000) -> dict:
    """测试配置：基于 DEFAULT_CONFIG 覆盖（hash 嵌入加速 + 独立临时目录）。"""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # 深拷贝
    cfg["paths"] = {
        "warm_cache": os.path.join(tmp, "coral_warm.json"),
        "cold_archive": os.path.join(tmp, "coral_cold.jsonl"),
        "vector_store": os.path.join(tmp, "coral_vectors.npy"),
        "vector_index": os.path.join(tmp, "coral_vector_index.json"),
    }
    cfg["threads"] = {"path": os.path.join(tmp, "coral_threads.json")}
    cfg["embedding"] = {"embedder": "hash", "dim": 128, "normalize": True}
    cfg["memory"]["capacity_threshold"] = capacity
    cfg["memory"]["governance_headroom"] = 2
    cfg["parallelism"]["embed_batch_window_ms"] = 0
    cfg["storage"]["vector_save_interval_seconds"] = 0.1
    return cfg


async def main() -> None:
    tmp = _mkdtemp(prefix="coral_threads_test_")
    print(f"临时目录: {tmp}")

    # ---------------- 全流程 ----------------
    print("\n[1] 创建 / 状态 / 推进 / 中断 / 恢复 / 归档 / 串联")
    coral = ThreeDogCoral(cfg=make_cfg(tmp))
    t1 = await coral.thread_create("发布 v2.0", "升级嵌入模型并优化检索性能", by="聊天A")
    t2 = await coral.thread_create("写 README", "补充线程功能文档", parent_thread_id=t1.thread_id, by="聊天A")
    ok("thread_create 返回 active", t1.status == "active")
    ok("thread_id 非空且稳定", len(t1.thread_id) == 16)
    ok("链路文件已落盘", os.path.exists(coral.path_threads))

    threads = await coral.thread_status()
    ok("thread_status 总览返回 2 条", len(threads) == 2)
    by_id = await coral.thread_status(thread_id=t1.thread_id)
    ok("按 ID 查详情", len(by_id) == 1 and by_id[0].thread_id == t1.thread_id)
    ok("总览未命中不存在的 ID", await coral.thread_status(thread_id="nope") == [])

    await coral.thread_advance(t1.thread_id, "migrate_bge.py 已跑通", done=True, by="聊天B")
    await coral.thread_advance(t1.thread_id, "向量重建完成，检索验证通过", by="聊天C")
    t1b = (await coral.thread_status(thread_id=t1.thread_id))[0]
    ok("advance 追加 2 个步骤", len(t1b.steps) == 2)
    ok("advance 记录推进者", t1b.last_advance_by == "聊天C" and t1b.steps[0]["by"] == "聊天B")
    ok("advance done 标记生效", t1b.steps[0]["done"] is True)

    await coral.thread_interrupt(t1.thread_id, reason="等上游依赖")
    t1c = (await coral.thread_status(thread_id=t1.thread_id))[0]
    ok("interrupt 状态变更", t1c.status == "interrupted")
    ok("interrupt 原因入步骤链", t1c.steps[-1]["text"].startswith("[中断]"))

    await coral.thread_resume(t1.thread_id)
    ok("resume 恢复 active", (await coral.thread_status(thread_id=t1.thread_id))[0].status == "active")

    await coral.thread_archive(t2.thread_id)
    overview = await coral.thread_status()
    ok("archive 后不出现在活跃总览", all(t.thread_id != t2.thread_id for t in overview))
    archived = await coral.thread_status(include_archived=True)
    ok("include_archived 能看到归档", any(t.thread_id == t2.thread_id for t in archived))
    ok("指定 ID 查归档仍可", len(await coral.thread_status(thread_id=t2.thread_id)) == 1)

    t3 = await coral.thread_create("子任务：压测")
    await coral.thread_link(t3.thread_id, t1.thread_id)
    t3b = (await coral.thread_status(thread_id=t3.thread_id))[0]
    ok("thread_link 建立父子关系", t3b.parent_thread_id == t1.thread_id)

    q = await coral.thread_status(query="压测")
    ok("query 过滤命中", len(q) == 1 and q[0].thread_id == t3.thread_id)
    q2 = await coral.thread_status(query="不存在的关键词")
    ok("query 过滤无命中", len(q2) == 0)

    # ---------------- 永不遗忘：淘汰免疫 ----------------
    print("\n[2] 永不遗忘：容量治理后线程仍在、记忆池不含线程")
    tmp2 = _mkdtemp(prefix="coral_gov_test_")
    small = ThreeDogCoral(cfg=make_cfg(tmp2, capacity=5))
    for i in range(30):
        await small.insert(f"第 {i} 条测试记忆，内容互不相同以免合并")
    s = small.stats()
    ok("治理后记忆总数收敛", s["total"] <= 5 + 2, f"total={s['total']}")
    ok("治理后线程计数不变", s["threads"] == 0 and len(await small.thread_status()) == 0)
    await small.thread_create("关键链路", "这条绝不能丢", by="压测")
    await small.insert("再来一条触发治理")
    s2 = small.stats()
    ok("触发治理后链路仍为 1", s2["threads"] == 1, f"threads={s2['threads']}")
    ok("链路不在热/温池", not any(
        m.content == "这条绝不能丢"
        for m in small.hot_memory + small.warm_memory
    ))
    # 记忆池检索里不会混入链路内容
    hits = await small.search("这条绝不能丢")
    ok("memory_search 不返回链路内容", all("绝不能丢" not in h.content for h in hits))

    # ---------------- 重启持久化 ----------------
    print("\n[3] 重启持久化")
    await coral.flush()   # 节流落盘契约：读盘/重启前显式落盘
    coral2 = ThreeDogCoral(cfg=make_cfg(tmp))
    threads2 = await coral2.thread_status(include_archived=True)
    ok("重启后加载全部链路", len(threads2) == 3, f"count={len(threads2)}")
    ok("重启后摘要/步骤/状态保留",
       {t.thread_id: (t.status, len(t.steps)) for t in threads2} == {
           t1.thread_id: ("active", 3),     # 2 推进 + 1 中断原因（resume 后）
           t2.thread_id: ("archived", 0),
           t3.thread_id: ("active", 0),
       })

    # ---------------- 跨实例（多聊天）共享 ----------------
    print("\n[4] 跨实例共享：聊天 B 进程看到聊天 A 建的链路")
    proj = _mkdtemp(prefix="coral_share_test_")
    cfg_a = make_cfg(proj)
    a = ThreeDogCoral(cfg=cfg_a)
    b = ThreeDogCoral(cfg=cfg_a)          # B 先启动，此时还没有链路
    await a.thread_create("跨聊天协作", "聊天 A 定宏观路径，B~F 各自推进", by="聊天A")
    await a.thread_advance((await a.thread_status())[0].thread_id, "B 完成第一步", by="聊天B")
    await a.flush()                       # 落盘后 B 才能读到（跨进程靠磁盘同步）
    seen = await b.thread_status()        # B 靠指纹检测自动重载
    ok("B 看到 A 建的链路", len(seen) == 1 and seen[0].title == "跨聊天协作")
    ok("B 看到 A/B 的推进记录", len(seen[0].steps) == 1 and seen[0].last_advance_by == "聊天B")
    await b.thread_advance(seen[0].thread_id, "C 推进第二步", by="聊天C")
    await b.flush()
    seen_a = await a.thread_status()
    ok("A 反向看到 C 的推进（双向同步）", seen_a[0].steps[-1]["by"] == "聊天C")

    # ---------------- stats ----------------
    print("\n[5] stats 线程计数")
    st = coral2.stats()
    ok("stats 含 threads 字段", st.get("threads") == 3, f"threads={st.get('threads')}")

    # 注：临时目录保留在 tests/_tmp_threads（已在 .gitignore），
    # 不删除——实例 atexit 兜底写盘需要目录存在。
    print(f"\n全部通过：{PASS} 项断言 ✅")


if __name__ == "__main__":
    asyncio.run(main())
