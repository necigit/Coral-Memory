# -*- coding: utf-8 -*-
"""
验证 LLM 蒸馏接入（临时目录 + 本地 mock OpenAI 兼容端点，绝不触碰真实 memory_data）：
1) 未配置 llm 段 -> _try_distill 跳过聚类（返回 0）；
2) 配置 mock 端点 -> 相似簇被压缩成单条摘要（total 显著下降），失败时优雅降级。

运行：python tests/test_distill.py
"""
import asyncio
import json
import os
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from three_dog_coral import ThreeDogCoral


class MockLLM(BaseHTTPRequestHandler):
    """本地 mock OpenAI /chat/completions：把输入压缩成固定摘要。"""

    def do_POST(self):
        length = int(self.headers.get("content-length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        user_msg = next(m["content"] for m in body["messages"] if m["role"] == "user")
        summary = "摘要: 用户每天喝咖啡的习惯（已压缩 %d 条相似记忆）" % (user_msg.count("- ") )
        resp = {"choices": [{"message": {"content": summary}}]}
        data = json.dumps(resp).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


def make_cfg(tmp: str, llm: dict) -> dict:
    return {
        "paths": {
            "warm_cache": os.path.join(tmp, "warm.json"),
            "cold_archive": os.path.join(tmp, "cold.jsonl"),
            "vector_store": os.path.join(tmp, "vec.npy"),
            "vector_index": os.path.join(tmp, "vec_idx.json"),
        },
        "embedding": {"embedder": "hash", "dim": 64},
        "memory": {
            "sim_threshold_hot": 0.95,
            "hot_ttl_hours": 0,
            "max_hot_entries": 100,
            "max_warm_entries": 200,
            "max_cold_entries": 1000,
            "capacity_threshold": 100,     # 小容量，让治理频繁触发
            "governance_headroom": 0,
            "distill_first": True,
            "distill_sim_threshold": 0.5,  # 放低阈值，让"咖啡"类相似句聚类
            "distill_min_cluster": 3,
        },
        "llm": llm,
        "reload": {"check_interval_seconds": 60},
    }


async def run_case(llm: dict, expect_distill: bool) -> int:
    tmp = tempfile.mkdtemp(prefix="coral-distill-")
    try:
        cfg = make_cfg(tmp, llm)
        p = os.path.join(tmp, "coral_config.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)
        coral = ThreeDogCoral(p)
        # 插入 20 条高度相似的记忆（容量 100，不足以触发；手动触发治理验证蒸馏）
        for i in range(20):
            await coral.insert(f"用户每天上午喝一杯冰镇拿铁咖啡，第 {i} 次提到这个习惯")
        await coral.flush()
        before = coral._count_total()
        # 手动调用治理：总 20 <= 100+headroom，不会触发 -> 直接调 _try_distill 验证蒸馏本身
        distilled = await coral._try_distill()
        after = coral._count_total()
        print(f"[{'配置端点' if expect_distill else '未配置'}] before={before} 蒸馏减少={distilled} after={after}")
        if expect_distill:
            assert distilled > 0, "配置端点后应发生蒸馏压缩"
            assert after < before, "蒸馏后总数应下降"
        else:
            assert distilled == 0, "未配置端点应跳过蒸馏"
        return distilled
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


async def main() -> None:
    # 起 mock LLM
    server = ThreadingHTTPServer(("127.0.0.1", 0), MockLLM)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        # 1) 未配置 -> 跳过
        await run_case({"base_url": "", "api_key": "", "model": "mock"}, expect_distill=False)
        print("[1] ✅ 未配置 LLM 端点：跳过蒸馏")
        # 2) 配置 mock 端点 -> 压缩
        await run_case(
            {"base_url": f"http://127.0.0.1:{port}/v1", "api_key": "sk-test", "model": "mock"},
            expect_distill=True,
        )
        print("[2] ✅ 配置端点：相似簇被压缩为摘要，总数下降")
        # 3) 端点挂了 -> 优雅降级（不抛异常、不删数据）
        await run_case(
            {"base_url": "http://127.0.0.1:1/v1", "api_key": "sk-test", "model": "mock"},
            expect_distill=False,
        )
        print("[3] ✅ 端点不可达：优雅降级，不影响治理")
        print("\n全部断言通过 ✅")
    finally:
        server.shutdown()


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    asyncio.run(main())
