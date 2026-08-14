# -*- coding: utf-8 -*-
"""
换嵌入模型迁移脚本（author: @Ne）
MiniLM(384) -> BAAI/bge-small-zh-v1.5(512)：
1. 修订 warm.json（去掉过时的"384维"描述；追加两条短句化要点记忆）
2. 用当前配置的嵌入器全量重嵌入 warm（+cold）记忆，重建向量区
3. 打印检索验证

用法: python migrate_bge.py
注意: 先停掉正在运行的 coral 进程（DSH 会 60-90s 后自动重连新进程）
"""
import asyncio
import hashlib
import json
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
sys.path.insert(0, BASE)

WARM_PATH = os.path.join(BASE, "memory_data", "coral_warm.json")
COLD_PATH = os.path.join(BASE, "memory_data", "coral_cold.jsonl")

# 追加的短句要点（B：短句化）
SHORT_ITEMS = [
    (
        "珊瑚已接入 DSH Harness：MCP stdio 桥 coral_mcp_server.py，工具 mcp__coral__memory_search / mcp__coral__memory_insert，配置在 cordis.patch.yml",
        0.7,
    ),
    (
        "排障教训：MCP SDK stdio 传输是 newline-delimited JSON（一条 JSON + 换行），不是 LSP Content-Length 帧",
        0.5,
    ),
]

# 修订的旧条目：content 替换（item_id 需按新 content 重算）
REWRITE = {
    "e806927cbc01400e": "记忆系统三级存储：热区/温区/冷区 + 向量存 .npy（当前 bge-small-zh 512 维）",
}


def gen_id(content: str) -> str:
    return hashlib.md5(content.encode("utf-8")).hexdigest()[:16]


def token_count(text: str) -> int:
    return int(len(text.encode("utf-8")) // 3.8)


def patch_warm() -> list:
    with open(WARM_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)
    now = time.time()
    new_items = []
    seen_ids = set()
    for d in raw:
        content = d.get("content", "")
        iid = d.get("item_id")
        if iid in REWRITE:
            content = REWRITE[iid]
            iid = gen_id(content)
            d = dict(d)
            d["item_id"] = iid
            d["content"] = content
            d["token_count"] = token_count(content)
        if iid in seen_ids:
            continue
        seen_ids.add(iid)
        new_items.append(d)
    for content, imp in SHORT_ITEMS:
        iid = gen_id(content)
        if iid in seen_ids:
            continue
        seen_ids.add(iid)
        new_items.append(
            {
                "item_id": iid,
                "content": content,
                "timestamp": now,
                "last_access": now,
                "token_count": token_count(content),
                "access_count": 1,
                "importance": imp,
            }
        )
    with open(WARM_PATH, "w", encoding="utf-8") as f:
        json.dump(new_items, f, ensure_ascii=False, indent=2)
    print(f"[warm] {len(raw)} -> {len(new_items)} 条")
    return new_items


def load_cold() -> list:
    if not os.path.exists(COLD_PATH):
        return []
    out = []
    with open(COLD_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    return out


async def main() -> None:
    from three_dog_coral import get_coral

    print("=== 1. 修订 warm.json ===")
    patch_warm()

    print("=== 2. 全量重嵌入并重建向量区 ===")
    c = get_coral(os.path.join(BASE, "coral_config.json"))
    items = list(c.warm_memory) + load_cold()
    texts = [m["content"] if isinstance(m, dict) else m.content for m in items]
    ids = [m["item_id"] if isinstance(m, dict) else m.item_id for m in items]
    print(f"待嵌入 {len(texts)} 条，维度配置 = {c.embedder._cfg.get('dim')}")
    vecs = c.embedder.embed(texts)
    print("嵌入完成 shape =", vecs.shape)
    for iid, v in zip(ids, vecs):
        c.vector_store.upsert(iid, v)
    c.vector_store.save()
    print("向量区已重建并落盘")

    print("=== 3. 检索验证 ===")
    for q in ["珊瑚怎么接进 DSH 的", "MCP 工具叫什么名字", "上次踩了什么坑", "作者是谁", "咖啡偏好"]:
        hits = await c.search(q, top_k=3)
        print(f"问「{q}」:")
        for h in hits:
            print(f"  [{h.score:.3f}] {h.content[:55]}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
