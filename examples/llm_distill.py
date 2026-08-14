# -*- coding: utf-8 -*-
"""
真实 LLM 蒸馏接入示例
=====================

占位版 `ThreeDogCoral._distill` 恒返回 None（跳过蒸馏）。本示例通过子类覆写
`_distill`，把相似记忆簇压缩成一条摘要 —— 超容治理时"先蒸馏后淘汰"才真正生效。

LLM 走 OpenAI 兼容的 `POST /chat/completions`（用 urllib，零额外依赖）：

    $env:LLM_BASE_URL = "http://127.0.0.1:11434/v1"   # 例如 Ollama
    $env:LLM_API_KEY  = "sk-..."
    $env:LLM_MODEL    = "gpt-4o-mini"
    python examples/llm_distill.py

不设置环境变量也能跑：蒸馏会打印提示并保持占位行为（返回 None -> 直接淘汰），
正好演示"接入前 / 接入后"两条路径。

运行：python examples/llm_distill.py
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
import urllib.request
from typing import Any, Dict, List, Optional

# ---- 仓库根目录引导（子目录直接运行时也能 import 根模块） ----
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from three_dog_coral import MemoryItem, ThreeDogCoral


class DistillingCoral(ThreeDogCoral):
    """接入 LLM 的蒸馏珊瑚：超容时把相似记忆压缩成摘要再淘汰。"""

    async def _distill(self, cluster: List[MemoryItem]) -> Optional[MemoryItem]:
        """覆写占位接口：cluster 是一批相似记忆，返回压缩后的摘要（或 None 跳过）。"""
        if not cluster:
            return None
        texts = [f"- {m.content}" for m in cluster]
        summary = await self._llm_summarize(texts)
        if not summary:
            return None
        now = time.time()
        return MemoryItem(
            item_id=self._gen_id(summary),
            content=summary,
            timestamp=now,
            last_access=now,
            token_count=self._count_token(summary),
            access_count=sum(m.access_count for m in cluster),   # 热度继承
            importance=max(m.importance for m in cluster),       # 重要性继承
        )

    async def _llm_summarize(self, texts: List[str]) -> Optional[str]:
        """OpenAI 兼容 chat/completions 调用；失败或未配置时返回 None。"""
        base = os.environ.get("LLM_BASE_URL", "").strip()
        api_key = os.environ.get("LLM_API_KEY", "").strip()
        model = os.environ.get("LLM_MODEL", "gpt-4o-mini").strip()
        if not base:
            print("  [llm_distill] 未设置 LLM_BASE_URL -> 跳过蒸馏（保持占位行为，直接淘汰）")
            return None
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "你是记忆压缩助手：把相似记忆合并成一条不超过 80 字的摘要，保留关键事实。"},
                {"role": "user", "content": "压缩下面这些相似记忆:\n" + "\n".join(texts)},
            ],
            "temperature": 0.2,
            "max_tokens": 200,
        }
        req = urllib.request.Request(
            base.rstrip("/") + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"content-type": "application/json", "authorization": f"Bearer {api_key}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["choices"][0]["message"]["content"].strip()
        except Exception as exc:  # noqa: BLE001
            print(f"  [llm_distill] LLM 调用失败（{exc}）-> 跳过蒸馏")
            return None


async def main() -> None:
    tmp = tempfile.mkdtemp(prefix="coral-distill-")
    try:
        cfg: Dict[str, Any] = {
            "paths": {
                "warm_cache": os.path.join(tmp, "warm.json"),
                "cold_archive": os.path.join(tmp, "cold.jsonl"),
                "vector_store": os.path.join(tmp, "vec.npy"),
                "vector_index": os.path.join(tmp, "vec_idx.json"),
            },
            "embedding": {"embedder": "hash", "dim": 384},
            "memory": {
                "sim_threshold_hot": 0.95,
                "hot_ttl_hours": 0,          # 立即过期 -> 温区（让候选池进蒸馏）
                "max_hot_entries": 100,
                "max_warm_entries": 200,
                "max_cold_entries": 1000,
                "capacity_threshold": 6,
                "governance_headroom": 0,    # 关闭余量，让治理即时触发
                "distill_first": True,
                "distill_sim_threshold": 0.5,
                "distill_min_cluster": 3,
            },
            "reload": {"check_interval_seconds": 60},
        }
        p = os.path.join(tmp, "coral_config.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)

        coral = DistillingCoral(p)
        # 插入一批相似记忆（20 条，容量 6；治理余量 floor=10，总 20 > 16 才触发）
        for i in range(20):
            await coral.insert(f"用户每天上午喝一杯冰镇拿铁咖啡，第 {i} 次提到这个习惯")
        await coral.flush()
        stats = coral.stats()
        print(f"\n蒸馏治理后: total={stats['total']} hot={stats['hot']} "
              f"warm={stats['warm']} cold={stats['cold']} vectors={stats['vectors']}")
        hits = await coral.search("喝咖啡的习惯")
        print("检索验证:")
        for h in hits[:3]:
            print(f"   {h.score:.3f}  {h.content[:40]}")
        print("\n提示：设置 LLM_BASE_URL/LLM_API_KEY/LLM_MODEL 后重跑，"
              "相似记忆会被压缩成单条摘要（total 下降更多）。")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    asyncio.run(main())
