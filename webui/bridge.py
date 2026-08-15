# -*- coding: utf-8 -*-
"""
脑珊瑚 GUI 桥（作者：@Ne）—— 把 host 路由的 HTTP 请求翻译成 three_dog_coral 的调用。

为什么存在：浏览器设置面板不能直连 MCP（浏览器侧无 MCP 客户端），
host 插件（lib/index.js）收到同源请求后 spawn 本脚本，复用珊瑚的全部逻辑
（report/config_get/config_set/config_reset），避免在 Node 侧重复实现。

用法:
    python -X utf8 bridge.py <action> <json_args> [coral_config.json]
    action: report | get_config | set_config | reset_config

stdout 输出 JSON（{"ok": true, ...}）；出错时 stderr 打印并退出码 1。
"""
import asyncio
import json
import os
import sys
import traceback

# 离线优先：模型走本地缓存（与 coral_mcp_server.py 同策略），避免 HF 联网挂起
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

# 让 three_dog_coral 可导入：开发仓库根（webui 上一级）或 npm 包内 runtime/（发布形态）都找。
# 本脚本在 webui/ 下：../ = 仓库根；./runtime/ = 包内运行时副本。
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_CORAL_ROOT = os.path.dirname(_THIS_DIR)
_RUNTIME_DIR = os.path.join(_THIS_DIR, "runtime")
for _P in (_RUNTIME_DIR, _CORAL_ROOT):
    if _P not in sys.path:
        sys.path.insert(0, _P)

# 关键：把进程 CWD 锚定到记忆数据目录（CORAL_DATA_DIR，host 插件注入），
# 缺省回退到仓库根（开发形态）。coral_config.json 里的 paths 是相对路径（memory_data/...），
# 按 CWD 解析；GUI host 从 DSH 进程 spawn 本脚本时，不锚定会让缓存落到错误目录。
# 与 coral_mcp_server.py 的 chdir 同一语义：路径永远相对配置所在处。
_DATA_DIR = os.environ.get("CORAL_DATA_DIR") or _CORAL_ROOT
os.chdir(_DATA_DIR)

from three_dog_coral import get_coral  # noqa: E402


async def _dispatch(coral, action: str, args: dict):
    if action == "report":
        return {"ok": True, **(await coral.report())}
    if action == "get_config":
        path = args.get("path")
        value = await coral.config_get(path)
        return {"ok": True, "path": path, "config": value}
    if action == "set_config":
        key_path = args.get("key_path")
        if not key_path or "value" not in args:
            raise ValueError("set_config 需要 key_path 与 value")
        r = await coral.config_set(key_path, args["value"])
        return {"ok": True, "key_path": r["key_path"], "old": r["old"], "new": r["new"]}
    if action == "reset_config":
        r = await coral.config_reset(args.get("key_path"))
        return {"ok": True, **r}
    if action == "set_paths":
        paths = args.get("paths")
        if not paths or not isinstance(paths, dict):
            raise ValueError("set_paths 需要 paths 字典（warm_cache/cold_archive/vector_store/vector_index/threads）")
        r = await coral.set_paths(paths)
        return {"ok": True, **r}
    raise ValueError(f"unknown action: {action}")


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "report"
    raw_args = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else "{}"
    config_path = sys.argv[3] if len(sys.argv) > 3 else None
    try:
        args = json.loads(raw_args) if raw_args.strip() else {}
        coral = get_coral(config_path)
        result = asyncio.run(_dispatch(coral, action, args))
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    raise SystemExit(main())
