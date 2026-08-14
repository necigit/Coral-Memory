# -*- coding: utf-8 -*-
"""
coral-memory MCP server (author: @Ne) —— 把脑珊瑚记忆库桥接成 MCP (Model Context Protocol) 服务器。

零依赖：手写 MCP stdio 传输（JSON-RPC 2.0 over stdin/stdout，LSP 风格 Content-Length 帧），
无需 pip install mcp。DSH Harness 通过 @deepseek-ai/dsh-mcp-client 插件以 stdio 方式拉起本进程，
工具会以 `mcp__coral__memory_search` / `mcp__coral__memory_insert` 出现在 Agent 工具列表。

用法:
    python coral_mcp_server.py            # 前台跑，等 MCP client 从 stdin 发 JSON-RPC

注册（在 $DSH_HOME/profiles/<profile>/cordis.patch.yml 加一条）:
    - id: mcp-coral
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: coral
        transport: stdio
        command: C:/Python313/python.exe
        args: ['./coral_mcp_server.py']
        toolCallTimeoutMs: 120000
"""
import asyncio
import json
import os
import sys
import threading
import time

# 让珊瑚配置/数据路径与脚本所在目录绑定，无论 DSH 从哪个 cwd 拉起本进程
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(_SCRIPT_DIR)
sys.path.insert(0, _SCRIPT_DIR)

from three_dog_coral import TOOL_REGISTRY, _to_jsonable  # noqa: E402

SERVER_INFO = {"name": "coral-memory-mcp", "version": "0.1.0"}


# ---------------------------------------------------------------------------
# MCP stdio 传输
# 官方 @modelcontextprotocol/sdk 用 newline-delimited JSON（每帧一行 JSON + \n，
# 见 sdk shared/stdio.js 的 serializeMessage/ReadBuffer）；部分实现用 LSP 风格
# Content-Length 帧。这里输入两种都兼容，输出统一 newline-delimited（与 SDK 一致）。
# ---------------------------------------------------------------------------
def _read_frame() -> str | None:
    """从 stdin 读一帧 JSON 文本；EOF 返回 None。"""
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None  # EOF
        if line in (b"\r\n", b"\n"):
            continue  # 空行：可能是 Content-Length 帧的头部结束，或空消息
        text = line.decode("utf-8", "replace").strip()
        if text.lower().startswith("content-length:"):
            # LSP 风格帧：头部逐行读到空行，再按长度读 body
            headers = {"content-length": text.lower().partition(":")[2].strip()}
            while True:
                hline = sys.stdin.buffer.readline()
                if not hline:
                    return None
                if hline in (b"\r\n", b"\n"):
                    break
                htext = hline.decode("utf-8", "replace").strip()
                if ":" in htext:
                    key, _, value = htext.partition(":")
                    headers[key.strip().lower()] = value.strip()
            try:
                length = int(headers.get("content-length", "0"))
            except ValueError:
                length = 0
            if length <= 0:
                return ""
            return sys.stdin.buffer.read(length).decode("utf-8", "replace")
        # newline-delimited JSON（@modelcontextprotocol/sdk 的格式）
        return text


def _write_frame(payload: dict) -> None:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.write(b"\n")
    sys.stdout.buffer.flush()


def _log(*parts: object) -> None:
    """日志走 stderr，绝不污染 stdout 协议通道。"""
    try:
        print("[coral-mcp]", *parts, file=sys.stderr, flush=True)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# 工具 schema 转换：coral 的 {field: {type, required, description}} -> JSON Schema
# ---------------------------------------------------------------------------
def _input_schema(parameters: dict) -> dict:
    properties, required = {}, []
    for name, meta in (parameters or {}).items():
        prop = {k: v for k, v in meta.items() if k not in ("required",)}
        properties[name] = prop
        if meta.get("required"):
            required.append(name)
    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def _tool_list() -> list:
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "inputSchema": _input_schema(spec.parameters),
        }
        for spec in TOOL_REGISTRY.values()
    ]


async def _run_tool(name: str, arguments: dict) -> dict:
    spec = TOOL_REGISTRY.get(name)
    if spec is None:
        raise KeyError(f"unknown tool: {name}")
    args = _to_jsonable(arguments or {})
    result = await spec.fn(**args)
    return _to_jsonable(result)


# ---------------------------------------------------------------------------
# JSON-RPC 分发
# ---------------------------------------------------------------------------
def _handle(message: dict) -> dict | None:
    method = message.get("method")
    msg_id = message.get("id")

    if method == "initialize":
        client_ver = ((message.get("params") or {}).get("protocolVersion")) or "2024-11-05"
        return {
            "protocolVersion": client_ver,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
        }

    if method == "ping":
        return {}

    if method == "tools/list":
        return {"tools": _tool_list()}

    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        try:
            result = asyncio.run(_run_tool(name, arguments))
            return {
                "content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
                "isError": False,
            }
        except Exception as exc:  # noqa: BLE001
            _log("tools/call failed:", type(exc).__name__, exc)
            return {
                "content": [{"type": "text", "text": f"{type(exc).__name__}: {exc}"}],
                "isError": True,
            }

    # 通知类（notifications/initialized、notifications/*）与未知方法：无响应
    _log("ignored method:", method)
    return None


def _reply(msg_id, result=None, error=None) -> None:
    payload: dict = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        payload["error"] = {"code": error[0], "message": error[1]}
    else:
        payload["result"] = result
    _write_frame(payload)


# ---------------------------------------------------------------------------
# 启动：进入主循环。嵌入器懒加载——首次 tools/call 自然加载（模型已缓存，
# 数秒完成；如超时可调大 DSH 侧 toolCallTimeoutMs）。
# 注意：不要在这里预热（多线程并发初始化珊瑚会互相干扰）。
# ---------------------------------------------------------------------------
def main() -> int:
    try:
        with open(os.path.join(_SCRIPT_DIR, ".mcp_started"), "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S") + " spawned\n")
    except Exception:  # noqa: BLE001
        pass
    _log("coral-memory MCP server ready, tools:", ", ".join(TOOL_REGISTRY))
    while True:
        frame = _read_frame()
        if frame is None:
            _log("stdin EOF, exiting")
            break
        if not frame.strip():
            continue
        try:
            message = json.loads(frame)
        except json.JSONDecodeError as exc:
            _log("bad JSON frame:", exc)
            continue
        if "id" not in message or message.get("method") is None:
            continue  # 通知
        try:
            result = _handle(message)
        except Exception as exc:  # noqa: BLE001
            _log("handler error:", type(exc).__name__, exc)
            _reply(message["id"], error=(-32603, f"{type(exc).__name__}: {exc}"))
            continue
        if result is not None:
            _reply(message["id"], result=result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
