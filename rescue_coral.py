#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
珊瑚急救模式 rescue_coral.py —— 零依赖自检/备份/恢复（防把自己改坏）

用法:
    python rescue_coral.py check            # 快速自检：语法 + 关键工具 + 数据文件（只读）
    python rescue_coral.py check --deep     # 深度自检：额外子进程 import 测试（超时保护）
    python rescue_coral.py backup           # 把当前 好版本 存为 last_good（含时间戳副本）
    python rescue_coral.py restore          # 用 last_good 覆盖当前文件（先自动备份坏的）
    python rescue_coral.py list             # 列出所有备份
    python rescue_coral.py --dir <根目录>    # 指定珊瑚项目根（默认本脚本同目录）

铁律（agent 用）：动珊瑚代码前必 check；改完后必 check + backup。
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CORE_FILES = ["three_dog_coral.py", "coral_mcp_server.py"]
# 关键工具（@register_tool 注册名），缺一即判坏
KEY_TOOLS = ["memory_insert", "memory_search", "memory_delete", "memory_flush",
             "coral_stats", "coral_config_get", "coral_config_set",
             "thread_create", "thread_status", "thread_advance", "thread_archive"]
# 关键数据文件（存在性检查）
KEY_DATA = ["memory_data/coral_warm.json", "memory_data/coral_vectors.npy",
            "memory_data/coral_threads.json"]


def fmt_ok(msg):
    print(f"  [OK] {msg}")


def fmt_bad(msg):
    print(f"  [✘] {msg}")


def api_probe() -> tuple[bool, str]:
    """探测珊瑚 webui 桥 API：GET /_dsh/coral/api?action=report。
    返回 (通不通, 说明)。注意：桥由 dsh web 加载珊瑚 webui 时挂载，
    返回 JSON 才算通；返回 SPA 页面 = 桥未挂载（设置页也会缺失）。"""
    url = "http://127.0.0.1:3080/_dsh/coral/api?action=report"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "CoralRescue/1.0"})
        with urllib.request.urlopen(req, timeout=10) as r:
            ct = (r.headers.get("Content-Type") or "").lower()
            if "json" in ct:
                return True, "通（珊瑚 webui 桥在线）"
            return False, "桥未挂载（返回 SPA 页面）——珊瑚 webui 未加载，设置页会缺失，需重启 dsh web"
    except Exception as exc:  # noqa: BLE001
        return False, f"连接失败 {str(exc)[:70]}（dsh web 未运行？）"


# ---------- check ----------
def check(root: Path, deep: bool) -> int:
    """自检。返回码：0=文件健康且 API 通；1=文件损坏（可 restore）；2=文件健康但 API 桥不通（需重启 dsh web）。"""
    ok = True
    print("== 珊瑚急救自检 ==")
    print(f"项目根: {root}")
    for name in CORE_FILES:
        p = root / name
        if not p.exists():
            fmt_bad(f"{name} 缺失")
            ok = False
            continue
        try:
            compile(p.read_bytes(), str(p), "exec")
            fmt_ok(f"{name} 语法通过 ({p.stat().st_size} B)")
        except SyntaxError as e:
            fmt_bad(f"{name} 语法错误: 行{e.lineno} {e.msg}")
            ok = False

    # 工具存在性（源码里找 @register_tool 装饰的 def）
    src = (root / "three_dog_coral.py").read_text(encoding="utf-8") if (root / "three_dog_coral.py").exists() else ""
    missing = [t for t in KEY_TOOLS if f"def {t}(" not in src and f"@register_tool" not in src]
    # 允许 tools 定义在别处：凡 def 名存在即算
    missing = [t for t in KEY_TOOLS if f"def {t}(" not in src]
    if missing:
        fmt_bad(f"关键工具缺失: {missing}")
        ok = False
    else:
        fmt_ok(f"关键工具 {len(KEY_TOOLS)} 个定义齐全")

    for d in KEY_DATA:
        p = root / d
        if p.exists():
            fmt_ok(f"数据文件存在: {d} ({p.stat().st_size} B)")
        else:
            fmt_bad(f"数据文件缺失: {d}")
            ok = False

    if deep:
        print("-- 深度 import 测试（子进程，60s 超时）--")
        code = f"import sys; sys.path.insert(0, {str(root)!r}); import three_dog_coral; print('IMPORT_OK')"
        try:
            r = subprocess.run([sys.executable, "-c", code], capture_output=True,
                               text=True, timeout=60, cwd=str(root))
            if r.returncode == 0 and "IMPORT_OK" in r.stdout:
                fmt_ok("three_dog_coral import 成功")
            else:
                fmt_bad(f"import 失败 rc={r.returncode}: {r.stderr[-300:]}")
                ok = False
        except subprocess.TimeoutExpired:
            fmt_bad("import 超时（60s，可能卡在模块级初始化）")
            ok = False
    # API 桥探测：珊瑚 webui 是否真的挂载着（设置页缺失的元凶就在这）
    api_ok, api_note = api_probe()
    if api_ok:
        fmt_ok(f"API 桥（/_dsh/coral/api）: {api_note}")
    else:
        fmt_bad(f"API 桥（/_dsh/coral/api）: {api_note}")
    if ok:
        if api_ok:
            print("== 自检结果: 健康 ✔（文件 + API 桥）==")
            return 0
        print("== 自检结果: 文件健康 ✔，但 API 桥不通（2）——珊瑚 webui 未加载，需重启 dsh web ==")
        return 2
    print("== 自检结果: 异常 ✘ 建议 restore（1）==")
    return 1


# ---------- backup / restore ----------
def rescue_dir(root: Path) -> Path:
    d = root / "_rescue"
    d.mkdir(exist_ok=True)
    return d


def backup(root: Path) -> Path:
    rd = rescue_dir(root)
    ts = time.strftime("%Y%m%d_%H%M%S")
    saved = []
    for name in CORE_FILES:
        p = root / name
        if not p.exists():
            continue
        stamped = rd / f"{name.replace('.py', '')}.{ts}.py"
        shutil.copy2(p, stamped)
        shutil.copy2(p, rd / f"{name}.last_good.py")  # 稳定名，restore 用
        saved.append(name)
    (rd / "manifest.txt").write_text(
        f"last backup: {ts} files: {','.join(saved)}\n", encoding="utf-8")
    print(f"已备份 {len(saved)} 个文件到 {rd}（含 last_good 稳定版）: {saved}")
    return rd


def restore(root: Path) -> bool:
    rd = rescue_dir(root)
    restored = False
    for name in CORE_FILES:
        good = rd / f"{name}.last_good.py"
        target = root / name
        if not good.exists():
            print(f"  [✘] 没有 {name}.last_good.py 备份，跳过")
            continue
        if target.exists():
            # 先把当前坏的挪进 _rescue/broken 留存
            ts = time.strftime("%Y%m%d_%H%M%S")
            shutil.copy2(target, rd / f"{name}.broken.{ts}.py")
        shutil.copy2(good, target)
        print(f"  [OK] 已用 last_good 恢复 {name}")
        restored = True
    if not restored:
        print("没有可恢复的备份（先跑 backup 建立基线）")
        return False
    print("恢复完成，请重启珊瑚服务（dsh web 或 MCP）后运行 check 确认")
    return True


def list_backups(root: Path):
    rd = rescue_dir(root)
    files = sorted(rd.glob("*.py"))
    if not files:
        print(f"{rd} 下没有备份")
        return
    print(f"备份目录: {rd}")
    for f in files:
        print(f"  {f.stat().st_size:>8} B  {f.name}")


def main() -> int:
    ap = argparse.ArgumentParser(description="珊瑚急救模式")
    ap.add_argument("--dir", default=str(ROOT), help="珊瑚项目根（默认本脚本目录）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_check = sub.add_parser("check")
    p_check.add_argument("--deep", action="store_true", help="额外做子进程 import 测试")
    sub.add_parser("backup")
    sub.add_parser("restore")
    sub.add_parser("list")
    args = ap.parse_args()
    root = Path(args.dir).resolve()

    if args.cmd == "check":
        return check(root, args.deep)
    if args.cmd == "backup":
        backup(root)
        return 0
    if args.cmd == "restore":
        return 0 if restore(root) else 1
    if args.cmd == "list":
        list_backups(root)
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
