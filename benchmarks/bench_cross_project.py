# -*- coding: utf-8 -*-
"""
跨项目共享缓存命中率对照实验
============================

三个配置对比（同一份数据，hash 嵌入，确定性 seed）：
  1. isolated       ：每项目独立缓存（现状）
  2. shared_global  ：全部项目共用一个缓存池
  3. shared_affinity：共享池 + 项目亲缘度重排（同项目/用户级记忆 +0.15 分）

场景数据：
  A = 技术文档翻译项目；B = 产品说明翻译项目（与 A 共享"接口/翻译/文档"词汇）
  C = 美食推荐项目（与翻译完全无关，测污染）
  S = 用户级共享偏好（翻译风格/术语，对 A、B 相关，对 C 无关）
  E = 全新"电商文案翻译"项目（冷启动：池里没有 E 的任何记忆）

指标：
  recall@5  = 查询的 Top-5 里至少 1 条相关的比例
  precision@5 = Top-5 中相关条目的平均占比
  冷启动    = E 项目查询的"有结果比例"与 top1 词面重合度

运行：python bench_cross_project.py
"""

import asyncio
import json
import os
import shutil
import sys
import tempfile

# ---- 仓库根目录引导（子目录直接运行时也能 import 根模块） ----
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from three_dog_coral import ThreeDogCoral

AFFINITY_BOOST = 0.15

PROJECT_MEM = {
    "A": [
        "A:接口 {n} 首次调用建立连接池，超时 {t} 秒",
        "A:缓存失效后需要重建索引并预热",
        "A:技术文档术语表：token 保留原文",
        "A:错误码 401 表示鉴权失败，需刷新令牌",
        "A:部署文档要求写明端口与防火墙规则",
        "A:版本号遵循语义化版本规范",
        "A:代码示例要贴实际可运行的片段",
        "A:性能压测报告显示 QPS 提升 30%",
        "A:回调函数注册后要记得注销防泄漏",
        "A:数据库连接池大小按 2 倍核数配置",
        "A:日志格式统一为 JSON 结构化输出",
        "A:灰度发布先放 5% 流量观察",
    ],
    "B": [
        "B:产品说明：接口长按三秒进入配对模式",
        "B:说明书里按键叫法要统一为按钮",
        "B:产品包装上印注册码和防伪标识",
        "B:指示灯红色表示故障，绿色表示就绪",
        "B:说明书附录要含故障排查表",
        "B:保修条款注明一年内免费换新",
        "B:产品参数表格用毫米为单位",
        "B:说明书语气要客观中性",
        "B:配件清单包括充电线、说明书、保修卡",
        "B:产品支持蓝牙 5.2 与 Wi-Fi 直连",
        "B:锂电池首次使用需充满 8 小时",
        "B:操作步骤编号要与图示一一对应",
    ],
    "C": [
        "C:这家店招牌是麻辣香锅，微辣偏麻",
        "C:甜品店的杨枝甘露芒果给得很足",
        "C:深夜食堂的烤串十点后打八折",
        "C:巷口面馆的牛肉面汤头浓郁",
        "C:咖啡店手冲单品豆每周四上新",
        "C:这家寿司店的芥末是现磨的",
        "C:火锅店毛肚七上八下最脆",
        "C:奶茶店推荐三分糖去冰",
        "C:早茶店的虾饺皮薄馅大",
        "C:烤肉店五花肉配生菜解腻",
        "C:这家店的辣度可以自己选微辣中辣特辣",
        "C:甜品店招牌舒芙蕾现做要等二十分钟",
    ],
}

SHARED_PREFS = [
    "S:用户偏好：技术类文档翻译风格要正式",
    "S:用户偏好：术语保留英文原文加中文注释",
    "S:用户偏好：法律与说明书类内容必须直译",
    "S:用户偏好：营销类文案可以意译突出卖点",
    "S:用户偏好：翻译时用'您'称呼客户",
    "S:用户偏好：代码注释要尽量简短",
    "S:用户偏好：标题一律用名词短语",
    "S:用户偏好：产品说明要用温柔亲切的口吻",
]

QUERIES = {
    "A": [
        "请把这段技术文档翻译成英文：连接池超时参数怎么配",
        "技术文档里 token 和缓存失效怎么说",
        "翻译部署文档：端口和防火墙规则",
        "接口鉴权失败的错误码怎么翻译",
        "性能压测报告里 QPS 提升的表述",
        "语义化版本号的规范翻译",
        "回调函数和内存泄漏的英文术语",
        "数据库连接池的配置说明翻译",
    ],
    "B": [
        "请把产品说明书翻译成英文：配对模式怎么进",
        "说明书里按钮和按键的翻译区别",
        "产品包装上的注册码怎么翻译",
        "指示灯颜色的故障说明翻译",
        "保修条款和免费换新的翻译",
        "产品参数表格的单位翻译",
        "蓝牙 5.2 和 Wi-Fi 直连的说明",
        "锂电池首次充电的注意事项翻译",
    ],
    "C": [
        "这家店的招牌菜是什么推荐一下",
        "附近有什么甜品店推荐",
        "深夜食堂营业到几点",
        "哪家面馆的牛肉面好吃",
        "咖啡店有什么手冲豆",
        "寿司店的芥末是现磨的吗",
        "火锅店的毛肚怎么涮最脆",
        "奶茶店有什么推荐甜度",
    ],
    "E": [  # 冷启动：全新"电商文案翻译"项目，池里没有任何 E 记忆
        "请把电商促销文案翻译成英文：限时特惠",
        "商品标题的关键词翻译技巧",
        "购物车结算页的按钮文案翻译",
        "会员积分规则的说明翻译",
        "退换货政策的官方表述",
        "新品上架的预告文案翻译",
        "客服自动回复模板翻译",
        "优惠券使用条件的翻译",
    ],
}


def build_coral(tmp: str) -> ThreeDogCoral:
    cfg = {
        "paths": {
            "warm_cache": os.path.join(tmp, "warm.json"),
            "cold_archive": os.path.join(tmp, "cold.jsonl"),
            "vector_store": os.path.join(tmp, "vec.npy"),
            "vector_index": os.path.join(tmp, "vec_idx.json"),
        },
        "embedding": {"embedder": "hash", "dim": 384},
        "memory": {
            "sim_threshold_hot": 0.95,
            "hot_ttl_hours": 1,
            "max_hot_entries": 500,
            "max_warm_entries": 500,
            "max_cold_entries": 2000,
            "capacity_threshold": 2000,
        },
        "retrieval": {"weights": {"vector": 0.6, "jaccard": 0.2, "time": 0.2},
                      "top_k": 5, "tau_days": 7.0, "include_cold": True},
        "reload": {"check_interval_seconds": 60},
    }
    os.makedirs(tmp, exist_ok=True)
    p = os.path.join(tmp, "coral_config.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)
    return ThreeDogCoral(p)


def relevant_set(project: str) -> set:
    """A/B 查询的相关集 = 本项目记忆 + 共享偏好；C 只算本项目（偏好与美食无关）。"""
    if project in ("A", "B"):
        return {f"{project}:{i}" for i in range(len(PROJECT_MEM[project]))} | {f"S:{i}" for i in range(len(SHARED_PREFS))}
    return {f"{project}:{i}" for i in range(len(PROJECT_MEM[project]))}


def is_relevant(hit_content: str, project: str) -> bool:
    return hit_content.startswith((f"{project}:", "S:")) if project in ("A", "B") else hit_content.startswith(f"{project}:")


async def main() -> None:
    tmp = tempfile.mkdtemp(prefix="coral-xproject-")
    try:
        results: dict = {}
        for project in ("A", "B", "C"):
            # ---------- 1) 隔离：每项目独立缓存 ----------
            iso = build_coral(os.path.join(tmp, "iso", project))
            for mem in PROJECT_MEM[project] + SHARED_PREFS:
                await iso.insert(mem)
            recall = precision = 0.0
            for q in QUERIES[project]:
                hits = await iso.search(q, top_k=5)
                rel = [h for h in hits if is_relevant(h.content, project)]
                if rel:
                    recall += 1
                precision += len(rel) / 5.0
            n = len(QUERIES[project])
            results.setdefault("isolated", {})[project] = {
                "recall@5": round(recall / n, 3), "precision@5": round(precision / n, 3),
            }

            # ---------- 2) 共享：一个池子 ----------
            shared = build_coral(os.path.join(tmp, "shared"))
            for p2 in ("A", "B", "C"):
                for mem in PROJECT_MEM[p2]:
                    await shared.insert(mem)
            for mem in SHARED_PREFS:
                await shared.insert(mem)
            recall = precision = 0.0
            for q in QUERIES[project]:
                hits = await shared.search(q, top_k=5)
                rel = [h for h in hits if is_relevant(h.content, project)]
                if rel:
                    recall += 1
                precision += len(rel) / 5.0
            n = len(QUERIES[project])
            results.setdefault("shared_global", {})[project] = {
                "recall@5": round(recall / n, 3), "precision@5": round(precision / n, 3),
            }

            # ---------- 3) 共享 + 项目亲缘度重排（Top-50 重排） ----------
            recall = precision = 0.0
            for q in QUERIES[project]:
                hits = await shared.search(q, top_k=50)
                ranked = sorted(
                    hits,
                    key=lambda h: h.score + (AFFINITY_BOOST if is_relevant(h.content, project) else 0.0),
                    reverse=True,
                )[:5]
                rel = [h for h in ranked if is_relevant(h.content, project)]
                if rel:
                    recall += 1
                precision += len(rel) / 5.0
            n = len(QUERIES[project])
            results.setdefault("shared_affinity", {})[project] = {
                "recall@5": round(recall / n, 3), "precision@5": round(precision / n, 3),
            }

        # ---------- 冷启动 E ----------
        iso_e = build_coral(os.path.join(tmp, "iso", "E"))
        e_hits_iso = 0
        for q in QUERIES["E"]:
            if await iso_e.search(q, top_k=5):
                e_hits_iso += 1
        hit_iso = e_hits_iso / len(QUERIES["E"])

        e_hits_shared = 0
        e_top1_overlap = 0
        for q in QUERIES["E"]:
            hits = await shared.search(q, top_k=5)
            if hits:
                e_hits_shared += 1
                if hits[0].scores["jaccard"] > 0:
                    e_top1_overlap += 1
        hit_shared = e_hits_shared / len(QUERIES["E"])
        top1_ov = e_top1_overlap / len(QUERIES["E"])

        # ---------- 汇总 ----------
        print("=" * 78)
        print("跨项目共享缓存命中率对照（hash 嵌入，seed 固定，每项目 8 条查询）")
        print("=" * 78)
        print(f"{'配置':<18}{'项目':<6}{'recall@5':<10}{'precision@5':<12}")
        for cfg_name, per_project in results.items():
            for pj, m in per_project.items():
                print(f"{cfg_name:<18}{pj:<6}{m['recall@5']:<10}{m['precision@5']:<12}")
        print("-" * 78)
        for cfg_name, per_project in results.items():
            avg_r = sum(m["recall@5"] for m in per_project.values()) / 3
            avg_p = sum(m["precision@5"] for m in per_project.values()) / 3
            print(f"{cfg_name:<18}{'均值':<6}{avg_r:<10}{avg_p:<12}")

        print("-" * 78)
        print(f"冷启动(E=电商文案翻译，池中无 E 记忆)：")
        print(f"  隔离：有结果比例 {hit_iso:.0%}（空池 -> 全 miss）")
        print(f"  共享：有结果比例 {hit_shared:.0%}，top1 词面重合 {top1_ov:.0%}"
              f"（复用 A/B 翻译记忆的句式与词汇）")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass
    asyncio.run(main())
