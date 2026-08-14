# 三狗珊瑚 · Coral Memory

> **A heat-aware persistent memory layer for LLM agents** — 面向 LLM Agent 的记忆层中间件：
> 三级存储（热/温/冷）、多路融合检索、热度生命周期淘汰、配置热加载、DSH Harness 插件集成。
>
> 作者：**@Ne**（2026-08-06 起源）· MIT License

---

## 对用户的价值（玩家视角，一句话版）

**"它让 AI 记住你的习惯和说过的话，每次只挑最相关的几条回忆出来用——不用你重复交代，也不用把整个聊天记录塞给 AI。"**

| 玩家问题 | 答案（实测数据） |
|---|---|
| **能提升命中率吗？** | 能。跨项目冷启动 **0% → 100%** 有结果；相关查询 recall@5 ≥ 0.95；共享+亲缘度方案 precision@5 = **1.0** |
| **能省上下文吗？** | 能。200 轮会话原文 ≈ 4927 token → 每次只注入 Top-5 相关记忆 ≈ **126 token**（省 **~97%**），且注入的是**相关记忆**而不是流水账 |
| **会越用越卡吗？** | 不会。热度淘汰自动清理没用的旧记忆；实测 2 万条写入 88.8s、检索 13ms/次 |
| **要重新说一遍吗？** | 不用。重复的偏好自动去重合并，命中过的记忆热度更高、更难被淘汰 |

## 特性

1. **存储**：热（内存）/ 温（内存 + JSON）/ 冷（JSONL 落盘）三级；每条记忆生成 384 维向量
   （本地 `sentence-transformers/all-MiniLM-L6-v2`，缺失时自动降级为确定性哈希嵌入），矩阵独立存 `.npy`。
2. **多路融合检索**：向量余弦 **0.6** + 关键词 Jaccard **0.2** + 时间衰减 **0.2**（τ 默认 7 天），返回 Top-K 带综合/分项得分。
3. **热度淘汰**：`H = 频率(0.4) + 最近访问(0.3) + 重要性(0.3)`（对数频率 + 池归一化）；
   超容量**先蒸馏再淘汰**（`_distill` 为可覆写的 LLM 接口，默认占位跳过）。
4. **配置热加载**：所有阈值来自 `coral_config.json`，运行时自动重载，无需重启。
5. **并发优化**：嵌入合批、位图 Jaccard 向量化（20×）、整池 BLAS 打分、治理余量批量淘汰、向量节流落盘。
6. **磁盘配额**：`storage.max_bytes` 警告线 + 硬线截断（按热度淘汰冷库）。
7. **Harness 集成**：`@register_tool` 注册 `memory_search` / `memory_insert`，可生成 DSH cordis 插件 JS + Sidecar 桥接。

## 快速开始

```bash
pip install numpy
python -c "import three_dog_coral; print(three_dog_coral.__version__)"   # 0.1.0
```

```python
import asyncio
from three_dog_coral import ThreeDogCoral

async def main():
    coral = ThreeDogCoral("coral_config.json")   # 全阈值来自 JSON，可热加载；缺文件会自动生成默认
    await coral.insert("用户喜欢喝冰镇拿铁咖啡", importance=0.7)
    hits = await coral.search("咖啡偏好", top_k=5)
    for h in hits:
        print(h.score, h.content, h.scores)      # 综合得分 + 分项得分

asyncio.run(main())
```

## 配置参考（`coral_config.json`）

| 段 | 关键项 | 默认 | 说明 |
|---|---|---|---|
| `memory` | `capacity_threshold` | 1000 | 记忆总数上限，超限先蒸馏再淘汰 |
| | `governance_headroom` | 0（自动） | 治理余量 `max(10, min(容量/10, 200))` |
| | `hot_ttl_hours` / `max_hot_entries` / `max_warm_entries` / `max_cold_entries` | 24/50/200/1000 | 三级存储参数 |
| `retrieval` | `weights` | 0.6/0.2/0.2 | 向量/Jaccard/时间 融合权重 |
| | `top_k` / `tau_days` / `include_cold` / `vectorized_jaccard` | 5/7/True/True | 检索参数 |
| `heat` | `weights` | 0.4/0.3/0.3 | 频率/最近访问/重要性 |
| | `cold_fold_interval_seconds` | 30 | 冷库热度增量落盘节流 |
| `storage` | `vector_save_interval_seconds` | 5.0 | 向量落盘节流（防 O(n²) 写盘） |
| | `max_bytes` / `warn_ratio` / `hard_ratio` | 0/0.8/0.85 | 磁盘配额（0 = 不限制） |
| `parallelism` | `embed_batch_window_ms` | 0 | 嵌入合批窗口（真实模型建议 4-8ms） |
| `reload` | `check_interval_seconds` | 2.0 | 配置 mtime 检测节流 |

## 实测基准

| 项目 | 结果 |
|---|---|
| 200 轮对话压测（翻译助手 + 20 轮冷却期画像） | 9 次画像、冷却期严格生效、容量精确收敛 |
| 跨项目共享 | 共享+亲缘度 recall/precision 双 1.0；冷启动 0%→100% |
| 2 万次暴力压测 | 写入 2 万条 88.8s、超容治理 90× 加速、重启一致 ✅ |
| 并行（8C/16T） | 嵌入合批 3.9×、位图 Jaccard 稳态 20×、8 路并发检索 5.4× |

## 适用边界 —— 什么时候它可能变成"负优化"

1. **跨项目无配额共享**：不同领域项目混池 → Top-5 被噪声挤占（precision 1.0 → 0.77 实测）。
   请用"共享池 + project 亲缘度 + 每项目配额"。
2. **无脑注入上下文**：相关记忆超过 ~5-10 条后边际收益为负。
3. **用 hash 嵌入冒充语义检索**：哈希嵌入只有词面重合，生产请装 sentence-transformers。
4. **小池子激进淘汰**：容量设太低 → recall 塌方。
5. **静默配置回退**：配置文件路径错误会回退默认，部署时请校验路径。

## DSH Harness 集成

```python
from three_dog_coral import build_dsh_cordis_plugin_js, MemoryToolSidecar

js = build_dsh_cordis_plugin_js("http://127.0.0.1:8765/rpc")  # 生成的插件源码（含 @Ne 水印注释）
sidecar = MemoryToolSidecar(port=8765)
sidecar.start()   # JS 的 execute 通过 HTTP 桥回 Python 注册表
# 把 js 交给 Agent 的 cordis_define（或写入 cordis.yml）即可注册 memory_search / memory_insert
```

## License

MIT（见 [LICENSE](LICENSE)）。作者 **@Ne** —— 二次开发请在代码中保留 `__author__` 与插件水印 🌱
