# -*- coding: utf-8 -*-
"""coral_mcp_server.py 冒烟测试：模拟 MCP client 走完整 stdio 协议。"""
import json
import os
import subprocess
import sys

SERVER = [sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), "coral_mcp_server.py")]


class McpClient:
    def __init__(self):
        self.proc = subprocess.Popen(
            SERVER,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        )

    def _send(self, payload: dict) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        frame = b"Content-Length: %d\r\n\r\n%s" % (len(data), data)
        self.proc.stdin.write(frame)
        self.proc.stdin.flush()

    def _recv(self) -> dict:
        headers = {}
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise EOFError("stdout closed")
            if line in (b"\r\n", b"\n"):
                break
            key, _, value = line.decode().strip().partition(":")
            headers[key.lower()] = value.strip()
        length = int(headers.get("content-length", "0"))
        body = self.proc.stdout.read(length) if length else b""
        return json.loads(body.decode("utf-8"))

    def request(self, method: str, params: dict, msg_id: int) -> dict:
        self._send({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params})
        return self._recv()

    def notify(self, method: str, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def close(self):
        self.proc.stdin.close()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
        err = self.proc.stderr.read().decode("utf-8", "replace")
        return err


def main() -> int:
    client = McpClient()
    try:
        # 1. initialize
        init = client.request(
            "initialize",
            {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "0"}},
            1,
        )
        print("[1] initialize ->", json.dumps(init.get("result"), ensure_ascii=False)[:200])
        assert "result" in init and init["result"]["serverInfo"]["name"] == "coral-memory-mcp"

        client.notify("notifications/initialized", {})

        # 2. tools/list
        listed = client.request("tools/list", {}, 2)
        tools = listed["result"]["tools"]
        print("[2] tools/list ->", [t["name"] for t in tools])
        assert {t["name"] for t in tools} >= {"memory_search", "memory_insert"}
        for t in tools:
            print("    ", t["name"], "inputSchema.required =", t["inputSchema"].get("required"))

        # 3. tools/call memory_search
        hit = client.request(
            "tools/call",
            {"name": "memory_search", "arguments": {"query": "咖啡偏好", "top_k": 2}},
            3,
        )
        text = hit["result"]["content"][0]["text"]
        print("[3] tools/call memory_search ->", text[:220])
        assert hit["result"].get("isError") in (None, False)

        # 4. tools/call memory_insert（smoke 条目）
        ins = client.request(
            "tools/call",
            {"name": "memory_insert", "arguments": {"content": "（smoke-test 记忆条目，可忽略）", "importance": 0.1}},
            4,
        )
        print("[4] tools/call memory_insert ->", ins["result"]["content"][0]["text"][:160])

        # 5. 未知工具 -> isError
        bad = client.request("tools/call", {"name": "no_such_tool", "arguments": {}}, 5)
        print("[5] tools/call unknown -> isError =", bad["result"].get("isError"))
        assert bad["result"].get("isError") is True

        print("\nSMOKE TEST PASSED")
        return 0
    finally:
        err = client.close()
        if err.strip():
            print("--- server stderr ---")
            print(err[-1500:])


if __name__ == "__main__":
    raise SystemExit(main())
