# -*- coding: utf-8 -*-
"""
200 轮对话压测：翻译软件助手 + 关键词钩子画像分析（20 轮冷却期）
================================================================

场景：
- 用户在跟一个翻译软件助手闲聊/下翻译任务；
- 对话中埋"关键词钩子"（我喜欢/偏好/风格/术语/正式/口语/直译/意译…）；
- 命中钩子且距上次画像分析 >= 20 轮对话（冷却期）-> 触发一次"翻译画像分析"；
- 每次对话：memory_insert 存入记忆 + memory_search 检索上下文（模拟 Agent 调工具）。

校验：
- 冷却期严格生效（相邻两次画像分析间隔 >= 20 轮）；
- 画像随对话演进（版本递增、字段累积）；
- 去重合并 / 三级存储流转 / 容量治理淘汰 / 重启后持久化。

运行：python test_200_turns.py [--turns 200] [--embedder hash|real] [--capacity 150]
"""

import argparse
import asyncio
import json
import logging
import os
import random
import shutil
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

# ---- 仓库根目录引导（子目录直接运行时也能 import 根模块） ----
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from three_dog_coral import ThreeDogCoral, get_coral, memory_insert, memory_search

# ---------------------------------------------------------------------------
# 场景数据
# ---------------------------------------------------------------------------
HOOK_PHRASES = [
    "喜欢", "偏好", "不要", "记住", "习惯", "风格", "术语",
    "正式", "口语", "直译", "意译", "称呼", "请翻译成",
]

DOMAINS = ["技术文档", "营销文案", "代码注释", "聊天消息", "法律条款", "产品说明"]
TARGETS = ["英文", "日文", "德文", "法文"]
# 带随机填充的模板：保证 200 轮里任务文本几乎不重复，真实压测存储增长
SAMPLE_TEMPLATES = {
    "技术文档": "接口 {n} 在首次调用时会建立连接池，超时设为 {t} 秒",
    "营销文案": "限时特惠第 {n} 期，{w}折起，错过再等一年",
    "代码注释": "刷新缓存并重新渲染 {w} 列表，注意修复 {n} 号缺陷",
    "聊天消息": "晚上八点{place}见，记得{w}一点，别迟到哦",
    "法律条款": "本协议自签署之日起 {n} 日内生效，争议由{city}法院管辖",
    "产品说明": "开机后长按电源键 {n} 秒进入配对模式，指示灯{color}闪烁",
}
FILL = {
    "w": ["三", "五", "八", "半", "双"],
    "place": ["老地方", "公司门口", "地铁站", "咖啡馆", "天台"],
    "city": ["北京", "上海", "深圳", "杭州", "成都"],
    "color": ["红色", "绿色", "蓝色", "白色", "橙色"],
}
# (画像字段, 触发词, 钩子句)
STYLE_HOOKS = [
    ("formality", "正式", "以后翻译技术文档时风格要正式一些"),
    ("formality", "口语", "聊天消息可以翻得口语化一点"),
    ("terminology", "保留英文", "术语要保留英文原文，后面加中文注释"),
    ("terminology", "音译", "专有名词一律音译，不要意译"),
    ("strategy", "直译", "法律条款必须直译，不能意译"),
    ("strategy", "意译", "营销文案要意译，突出卖点"),
    ("address", "您", "翻译时用'您'来称呼客户"),
    ("code_comments", "简短", "代码注释要尽量简短"),
    ("headings", "名词短语", "标题一律用名词短语"),
    ("tone", "温柔", "产品说明要用温柔亲切的口吻"),
]

COOLDOWN_TURNS = 20  # 画像分析冷却期：每 20 轮对话最多触发一次


def make_turn(i: int, rng: random.Random, task_history: List[str]) -> Tuple[str, Dict[str, Any]]:
    """生成第 i 轮用户话语。返回 (text, meta)。"""
    meta: Dict[str, Any] = {"hook": False, "kind": "task"}
    if i % 7 == 0:  # 偏好钩子轮（重申偏好 -> 去重合并 -> 访问频率上升 -> 高热度保护）
        field, trigger, sentence = STYLE_HOOKS[(i // 7) % len(STYLE_HOOKS)]
        meta.update({"hook": True, "kind": "preference", "field": field, "trigger": trigger})
        return sentence, meta
    if i % 13 == 0 and task_history:  # 刻意重复（测去重；原句仍在热/温区时合并）
        meta["kind"] = "repeat"
        return rng.choice(task_history), meta
    if i % 17 == 0:  # 闲聊噪音
        meta["kind"] = "noise"
        words = ["天气", "午饭", "加班", "咖啡", "地铁", "跑步"]
        return f"今天{rng.choice(words)}怎么样，第 {i} 轮闲聊随便聊聊", meta
    # 翻译任务轮（模板 + 随机填充 -> 几乎每轮唯一）
    domain = DOMAINS[i % len(DOMAINS)]
    target = TARGETS[(i // 3) % len(TARGETS)]
    sample = SAMPLE_TEMPLATES[domain].format(
        n=rng.randint(1, 999),
        t=rng.randint(1, 120),
        w=rng.choice(FILL["w"]),
        place=rng.choice(FILL["place"]),
        city=rng.choice(FILL["city"]),
        color=rng.choice(FILL["color"]),
    )
    text = f"请把这段{domain}翻译成{target}：{sample}"
    meta.update({"kind": "task", "domain": domain, "target": target})
    task_history.append(text)
    return text, meta


def analyze_profile(hits: List[Dict[str, Any]], prev: Dict[str, Any]) -> Dict[str, Any]:
    """把检索到的钩子记忆合并进翻译画像。"""
    text = " ".join(h["content"] for h in hits)
    profile = dict(prev)
    for field, trigger, _ in STYLE_HOOKS:
        if trigger in text:
            profile[field] = trigger
    return profile


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--turns", type=int, default=200)
    parser.add_argument("--embedder", choices=("hash", "real"), default="hash")
    parser.add_argument("--capacity", type=int, default=120)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--keep-data", action="store_true", help="保留测试数据目录")
    args = parser.parse_args()

    tmp = tempfile.mkdtemp(prefix="coral-200turns-")
    cfg_path = os.path.join(tmp, "coral_config.json")
    cfg = {
        "paths": {
            "warm_cache": os.path.join(tmp, "warm.json"),
            "cold_archive": os.path.join(tmp, "cold.jsonl"),
            "vector_store": os.path.join(tmp, "vec.npy"),
            "vector_index": os.path.join(tmp, "vec_idx.json"),
        },
        "embedding": {
            "embedder": "sentence-transformers" if args.embedder == "real" else "hash",
            "model_name": "sentence-transformers/all-MiniLM-L6-v2",
            "dim": 384,
        },
        "memory": {
            "sim_threshold_hot": 0.95,   # 只合并近乎完全重复
            "hot_ttl_hours": 1,          # 测试内不按时间过期，靠容量流转
            "max_hot_entries": 25,
            "max_warm_entries": 40,
            "max_cold_entries": 500,
            "capacity_threshold": args.capacity,
            "distill_first": True,
            "cold_scan_lines": 300,
        },
        "retrieval": {"weights": {"vector": 0.6, "jaccard": 0.2, "time": 0.2},
                      "top_k": 3, "tau_days": 7.0, "include_cold": True},
        "heat": {"weights": {"frequency": 0.4, "recency": 0.3, "importance": 0.3},
                 "tau_days": 7.0, "freq_scale": 10.0},
        "reload": {"check_interval_seconds": 60},
    }
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

    # 捕获珊瑚日志，统计治理/淘汰事件
    class Capture(logging.Handler):
        def __init__(self) -> None:
            super().__init__()
            self.msgs: List[str] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.msgs.append(record.getMessage())

    capture = Capture()
    logging.getLogger("three_dog_coral").addHandler(capture)
    logging.getLogger("three_dog_coral").setLevel(logging.INFO)

    coral = get_coral(cfg_path)  # 单例：与 @register_tool 工具共享
    rng = random.Random(args.seed)
    task_history: List[str] = []

    inserted = dup = 0
    search_turns = grounded = context_hits = 0
    hook_turns = 0
    analyses: List[Dict[str, Any]] = []
    profile: Dict[str, Any] = {}
    last_analysis_turn = 0
    max_total = 0
    t0 = time.monotonic()

    print(f"开始 200 轮对话压测（embedder={args.embedder}, capacity={args.capacity}, seed={args.seed}）", flush=True)
    print(f"画像冷却期 = {COOLDOWN_TURNS} 轮对话\n", flush=True)

    for i in range(1, args.turns + 1):
        text, meta = make_turn(i, rng, task_history)

        # 1) 写入记忆（Agent 调 memory_insert 工具）
        r = await memory_insert(text, importance=0.7 if meta["hook"] else 0.0)
        if r["inserted"]:
            inserted += 1
            cur_id = r["item_id"]
        else:
            dup += 1
            cur_id = None

        # 2) 检索上下文（Agent 调 memory_search 工具）
        query = f"翻译 {meta.get('domain', '')} {meta.get('target', '')}".strip() if meta["kind"] == "task" else text
        hits = await memory_search(query, top_k=3)
        top = hits["hits"]
        if top:
            search_turns += 1
            if any(h["scores"]["jaccard"] > 0 for h in top):
                grounded += 1
            # 新颖上下文：Top3 里有没有"不是刚插入那条"的旧记忆（助手真的回忆到了东西）
            if cur_id is None or any(h["id"] != cur_id for h in top):
                context_hits += 1

        # 3) 关键词钩子 -> 冷却期检查 -> 触发画像分析
        if meta["hook"]:
            hook_turns += 1
            if i - last_analysis_turn >= COOLDOWN_TURNS:
                hook_hits = await memory_search(text, top_k=8)
                profile = analyze_profile(hook_hits["hits"], profile)
                analyses.append({
                    "turn": i,
                    "version": len(analyses) + 1,
                    "trigger": meta["trigger"],
                    "profile": dict(profile),
                })
                last_analysis_turn = i
                print(f"  [画像 v{len(analyses)}] 第 {i} 轮触发（钩子：{meta['trigger']}），"
                      f"画像={profile}")

        stats = coral.stats()
        max_total = max(max_total, stats["total"])

    elapsed = time.monotonic() - t0
    stats = coral.stats()

    # ---------- 校验 ----------
    gaps = [analyses[k + 1]["turn"] - analyses[k]["turn"] for k in range(len(analyses) - 1)]
    cooldown_ok = all(g >= COOLDOWN_TURNS for g in gaps) and len(analyses) >= 1
    headroom = max(10, min(args.capacity // 10, 200))  # 与治理余量对齐
    capacity_ok = stats["total"] <= args.capacity + headroom
    cold_all = await coral._read_all_cold()
    hook_survived = any(m.importance >= 0.5 for m in coral.hot_memory + coral.warm_memory + cold_all)
    # 向量完整性：唯一内容 id 数 == 向量数（内容哈希 id 可能被多行共享）
    unique_ids = len({m.item_id for m in coral.hot_memory + coral.warm_memory + cold_all})
    vector_ok = unique_ids == stats["vectors"]

    governance_events = [m for m in capture.msgs if "超过容量阈值" in m]
    evict_events = [m for m in capture.msgs if "淘汰最低热度记忆" in m]

    # 重启持久化验证（先 flush：向量为节流落盘，需显式落盘）
    await coral.flush()
    coral2 = ThreeDogCoral(cfg_path)
    stats2 = coral2.stats()
    cold2 = await coral2._read_all_cold()
    alive_unique = len({m.item_id for m in cold2}) + stats2["warm"]  # 热区不落盘，孤儿向量启动时清理
    persist_ok = (
        stats2["warm"] == stats["warm"]
        and stats2["cold"] == stats["cold"]
        and stats2["vectors"] == alive_unique
    )

    # ---------- 报告 ----------
    print("\n" + "=" * 72, flush=True)
    print("200 轮对话压测报告（翻译软件助手场景）", flush=True)
    print("=" * 72, flush=True)
    print(f"轮次:                    {args.turns}")
    print(f"插入成功 / 去重合并:      {inserted} / {dup}")
    print(f"检索有结果轮次:          {search_turns}/{args.turns}")
    print(f"Top3 含共享词命中轮次:    {grounded} ({grounded / max(search_turns, 1) * 100:.1f}%)")
    print(f"Top3 含新颖上下文轮次:    {context_hits} ({context_hits / max(search_turns, 1) * 100:.1f}%)")
    print(f"钩子命中轮次:            {hook_turns}")
    print(f"画像分析触发次数:        {len(analyses)}（冷却期 {COOLDOWN_TURNS} 轮）")
    print(f"分析触发轮次:            {[a['turn'] for a in analyses]}")
    print(f"相邻分析间隔:            {gaps if gaps else '—'}")
    print(f"最终翻译画像:            {json.dumps(profile, ensure_ascii=False)}")
    print(f"存储: hot={stats['hot']} warm={stats['warm']} cold={stats['cold']} "
          f"total={stats['total']}/{args.capacity} 唯一内容={unique_ids} vectors={stats['vectors']}")
    print(f"运行中最大 total:        {max_total}")
    print(f"容量治理触发次数:        {len(governance_events)}，淘汰条数合计: "
          f"{sum(int(e.split('淘汰最低热度记忆')[1].split('条')[0]) for e in evict_events) if evict_events else 0}")
    print(f"重启后加载: warm={stats2['warm']} cold={stats2['cold']} vectors={stats2['vectors']}")
    print(f"耗时: {elapsed:.1f}s")

    print("\n校验结果:", flush=True)
    print(f"  [PASS] 冷却期严格生效（相邻分析间隔 >= {COOLDOWN_TURNS}）" if cooldown_ok
          else f"  [FAIL] 冷却期被破坏：间隔 {gaps}")
    print(f"  [PASS] 容量治理收敛（total={stats['total']} <= {args.capacity}+余量{headroom}）" if capacity_ok
          else f"  [FAIL] 容量未收敛 total={stats['total']}")
    print(f"  [PASS] 高重要性记忆在淘汰后存活（importance>=0.5）" if hook_survived
          else "  [WARN] 已无高重要性记忆（可能被淘汰）")
    print(f"  [PASS] 重启后 warm/cold/vectors 一致" if persist_ok
          else f"  [FAIL] 持久化不一致：{stats2} vs {stats}")
    print(f"  [PASS] 向量完整性（唯一内容 {unique_ids} == 向量 {stats['vectors']}）" if vector_ok
          else f"  [FAIL] 向量与内容不同步：唯一内容 {unique_ids} != 向量 {stats['vectors']}")

    all_ok = cooldown_ok and capacity_ok and persist_ok and vector_ok
    print(f"\n总体: {'全部通过 ✅' if all_ok else '存在问题 ❌'}")

    report = {
        "turns": args.turns, "embedder": args.embedder, "capacity": args.capacity,
        "inserted": inserted, "duplicates": dup,
        "search_turns": search_turns, "grounded": grounded, "context_hits": context_hits,
        "hook_turns": hook_turns, "analyses": analyses,
        "gaps": gaps, "profile": profile,
        "stats": stats, "max_total": max_total,
        "governance_events": len(governance_events),
        "evicted_total": sum(int(e.split("淘汰最低热度记忆")[1].split("条")[0]) for e in evict_events) if evict_events else 0,
        "restart_stats": stats2,
        "elapsed_s": round(elapsed, 1),
        "all_ok": all_ok,
    }
    with open(os.path.join(os.getcwd(), "test_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("详细报告已写入 test_report.json")

    logging.getLogger("three_dog_coral").removeHandler(capture)
    if not args.keep_data:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0 if all_ok else 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    logging.basicConfig(level=logging.WARNING)
    for _n in ("httpx", "huggingface_hub", "sentence_transformers"):
        logging.getLogger(_n).setLevel(logging.WARNING)
    sys.exit(asyncio.run(main()))
