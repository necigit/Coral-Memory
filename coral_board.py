#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
珊瑚任务表 coral_board.py —— 读 coral_threads.json 输出一张管理表，零依赖零常驻。

用法:
    python coral_board.py                  # 读默认路径 memory_data/coral_threads.json
    python coral_board.py --path xxx.json  # 指定文件
    python coral_board.py --candidates     # 只列「完工候选」的活跃链（方便归档清理）
"""
import argparse
import json
import sys
import time
from pathlib import Path

# 完工标记词（summary 或最后一步含这些词 -> 完工候选）
DONE_MARKERS = ("收官", "圆满", "全功能上线", "推送完成", "已发布", "全部交付",
                "彻底解决", "最终", "完成✅", "上线")
ACTIVE = "active"


def load(path: Path) -> list:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, list) else list(data.values())


def is_done_candidate(t: dict) -> bool:
    if t.get("status") != ACTIVE:
        return False
    steps = t.get("steps") or []
    if steps and all(s.get("done") for s in steps):
        return True
    blob = " ".join([str(t.get("summary") or "")] + [str(s.get("text") or "") for s in steps[-1:]])
    return any(m in blob for m in DONE_MARKERS)


def age_str(ts: float) -> str:
    if not ts:
        return "?"
    sec = time.time() - ts
    if sec < 0:
        return "刚刚"
    if sec < 3600:
        return f"{int(sec // 60)}分钟前"
    if sec < 86400:
        return f"{sec / 3600:.1f}小时前"
    return f"{sec / 86400:.1f}天前"


def main() -> int:
    ap = argparse.ArgumentParser(description="珊瑚任务表：推理线索管理总览")
    ap.add_argument("--path", default=str(Path(__file__).resolve().parent / "memory_data" / "coral_threads.json"))
    ap.add_argument("--candidates", action="store_true", help="只列完工候选")
    args = ap.parse_args()

    p = Path(args.path)
    if not p.exists():
        print(f"[err] 找不到线程文件: {p}", file=sys.stderr)
        return 1
    threads = load(p)

    stats = {}
    for t in threads:
        stats[t.get("status", "?")] = stats.get(t.get("status", "?"), 0) + 1
    if args.candidates:
        cands = [t for t in threads if is_done_candidate(t)]
        print(f"完工候选 {len(cands)} 条（建议 thread_archive 归档，内容永不遗忘，可 resume 恢复）\n")
        for t in cands:
            steps = t.get("steps") or []
            print(f"  {t['thread_id'][:8]}  {age_str(t.get('updated_at'))}  "
                  f"[{sum(1 for s in steps if s.get('done'))}/{len(steps)}步]  {t['title']}")
        return 0

    print(f"珊瑚任务表 —— 活跃 {stats.get('active', 0)} | 已归档 {stats.get('archived', 0)} | "
          f"中断 {stats.get('interrupted', 0)} | 共 {len(threads)} 条")
    print("-" * 100)
    order = {"active": 0, "interrupted": 1, "archived": 2}
    for t in sorted(threads, key=lambda x: (order.get(x.get("status"), 9), -(x.get("updated_at") or 0))):
        st = t.get("status", "?")
        mark = {"active": "▶", "interrupted": "⏸", "archived": "▦"}.get(st, "?")
        steps = t.get("steps") or []
        done_n = sum(1 for s in steps if s.get("done"))
        cand = "●归档?" if is_done_candidate(t) else ""
        print(f"{mark} {st:<11} {done_n:>2}/{len(steps):<2}步  {age_str(t.get('updated_at')):<9} "
              f"{(t.get('last_advance_by') or '')[:12]:<12} {cand:<5} {t['title'][:38]}")
    print("-" * 100)
    print("●归档? = 完工候选（步骤全 done 或 summary/末步含完成词），确认完工可 thread_archive")
    return 0


if __name__ == "__main__":
    sys.exit(main())
