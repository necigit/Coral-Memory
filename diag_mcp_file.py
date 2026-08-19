# -*- coding: utf-8 -*-
"""启动级自检（文件版）：coral MCP server 全 stdio 走文件，零管道变量。
发 initialize + tools/list 两帧（同一输入文件，读完 EOF 服务自动退出），
从输出文件解析两个响应，验证新 schema 生效。不触发嵌入器/数据读写/GPU。"""
import json
import os
import subprocess
import sys
import time

d = os.path.dirname(os.path.abspath(__file__))
INF = os.path.join(d, "_diag_in.bin")
OUTF = os.path.join(d, "_diag_out.txt")
ERRF = os.path.join(d, "_diag_err.txt")


def frame(msg_id: int, method: str, params: dict) -> bytes:
    data = json.dumps({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params},
                      ensure_ascii=False).encode("utf-8")
    return b"Content-Length: %d\r\n\r\n%s" % (len(data), data)


frames = (
    frame(1, "initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                            "clientInfo": {"name": "diag2", "version": "0"}})
    + frame(2, "tools/list", {})
)
with open(INF, "wb") as fh:
    fh.write(frames)

t0 = time.time()
with open(INF, "rb") as fi, open(OUTF, "wb") as fo, open(ERRF, "wb") as fe:
    p = subprocess.Popen([sys.executable, os.path.join(d, "coral_mcp_server.py")],
                         stdin=fi, stdout=fo, stderr=fe, cwd=d)
    try:
        rc = p.wait(timeout=45)
        print("server exited rc =", rc, "in %.1fs" % (time.time() - t0))
    except subprocess.TimeoutExpired:
        print("server still alive after 45s -> killing; (startup blocked)")
        p.kill()
        p.wait(timeout=5)

print("--- stdout file (lines) ---")
out = open(OUTF, encoding="utf-8", errors="replace").read()
lines = [ln for ln in out.splitlines() if ln.strip()]
print("resp lines:", len(lines))
for ln in lines:
    try:
        obj = json.loads(ln)
        res = obj.get("result") or {}
        if "serverInfo" in res:
            print("  [init] serverInfo:", res["serverInfo"])
        if "tools" in res:
            tools = res["tools"]
            ms = next((t for t in tools if t["name"] == "memory_search"), None)
            print("  [tools/list] count:", len(tools))
            print("  [tools/list] memory_search props:", sorted(ms["inputSchema"]["properties"]) if ms else "MISSING",
                  "| required:", ms["inputSchema"].get("required") if ms else None)
    except Exception as exc:  # noqa: BLE001
        print("  [unparsed line]", repr(ln[:80]), exc)
print("--- stderr file ---")
print(open(ERRF, encoding="utf-8", errors="replace").read()[:600] or "(empty)")
for f in (INF, OUTF, ERRF):
    try:
        os.remove(f)
    except OSError:
        pass
