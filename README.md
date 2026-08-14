# 三狗珊瑚 · Coral Memory

> **A heat-aware persistent memory layer for LLM agents** — 面向 LLM Agent 的记忆层中间件：
> 三级存储（热/温/冷）、多路融合检索、热度生命周期淘汰、配置热加载、DSH Harness 插件集成。

---

## Author — Mr. Code Muggle (@Ne)

Mr. Code Muggle — hi guys, I made something fun to play with: fork it, break it, rebuild it — just maybe mention me (lol).
The coral remembers what I can't. Questions? 📮 751286928@qq.com
Shoutout to every open-source maker out there 🌱

---

## 对用户的价值（玩家视角）

**"它让 AI 记住你的习惯和说过的话，每次只挑最相关的几条回忆出来用——不用你重复交代，也不用把整个聊天记录塞给 AI。"**

| 玩家问题 | 答案（标注前提与来源） |
|---|---|
| **能提升命中率吗？** | 基准测试下能。跨项目共享池场景冷启动 **0% → 100%** 有结果；相关查询 recall@5 ≥ 0.95；共享+亲缘度方案 precision@5 = **1.0**（合成语料、hash 嵌入的确定性基准，真实场景随语料/模型浮动） |
| **能省上下文吗？** | **有条件地能**。前提：应用把"全量会话历史"替换为"Top-5 相关记忆"注入。该前提下实测 200 轮会话原文 ≈ 4927 token → 每次注入 ≈ 126 token（省 ~97%）。若应用本来就只带最近几轮对话，记忆层带来的是**长期记忆与个性化**，不是 token 节省 |
| **会越用越卡吗？** | 不会。热度淘汰自动清理没用的旧记忆；实测 2 万条写入 88.8s、检索 13ms/次 |
| **要重新说一遍吗？** | 不用。重复的偏好自动去重合并，命中过的记忆热度更高、更难被淘汰 |

> ⚠️ **数字边界**：本表所有数字来自仓库内**确定性基准**（合成语料、hash 嵌入、固定 seed），
> 用途是**证明能力上限**，不是效果承诺。真实项目的结果取决于：语料分布、嵌入模型
> （装 sentence-transformers 语义更准）、检索权重、以及应用如何注入记忆。
> 记忆层本身不产生也不节省 token——它让"注入相关而非全量"这个选择变得可能。

---

## 架构：三级存储与数据流

```
                  ┌────────────┐   过期(TTL)   ┌────────────┐
  insert ──embed──▶   热区     ├──────────────▶│    温区     │
                  │ 内存列表   │              │ 内存+JSON   │
                  └─────┬──────┘              └─────┬──────┘
                        │ 超 max_hot（LRU 直通）     │ 超 max_warm
                        ▼                           ▼
                  ┌─────────────────────────────────────┐
                  │   冷区：coral_cold.jsonl（追加写）     │
                  │   检索只读尾部最新 N 行（流式读）      │
                  └─────────────────────────────────────┘
                  向量区：coral_vectors.npy（独立并行维护）
```

- **热区**（内存）：最快；按 `hot_ttl_hours` 过期进温区；超 `max_hot_entries` 时**直接 LRU 落冷**（不经过温区，避免中间层堆积——沿用旧版踩坑后的设计）；
- **温区**（内存 + `coral_warm.json`）：每次治理写盘，热度统计随写盘持久化；
- **冷区**（JSONL 追加）：`_dump_cold` 逐行追加零开销；检索用**尾部流式读**（向后分块，只读最后 64KB×N，不读全文件）；
- **向量区**：float32 矩阵独立存 `.npy` + id 索引 `.json`，与文本并行增删；**启动时清理孤儿向量**（热区不落盘，重启后其向量无主）。

**插入流程**：嵌入（锁外）→ 热/温查重（Jaccard ≥ `sim_threshold_hot` 则合并访问统计）→ 写向量 → 入热区 → 治理检查。
**检索流程**：查询嵌入（锁外）→ 热/温全量 + 冷区尾部打分 → 融合排序 → Top-K → 命中条目热度 +1（冷库记入待折叠增量）。

## 核心算法

**多路融合检索**（`retrieval.weights`，默认 0.6/0.2/0.2）：

```
score = 0.6·cos(vec(q), vec(m)) + 0.2·Jaccard(q, m) + 0.2·exp(-ΔT / τ)      # τ 默认 7 天
```

- 向量：整池一次 `np.stack` + BLAS 矩阵乘（GIL 外，2000 条 ~2ms）；
- Jaccard：2048-bit 哈希位图 + `numpy.bitwise_count` 一次向量化，位图跨查询缓存（基准下稳态 **20×** 加速）；
- 时间衰减：`exp(-ΔT/τ)`，ΔT 为记忆年龄。

**热度分**（`heat.weights`，默认 0.4/0.3/0.3）：

```
H = 0.4·log2(1+c)/log2(1+scale) + 0.3·exp(-Δt/τ) + 0.3·importance
```

- 频率用对数刻度（避免线性饱和），淘汰/蒸馏时按**池内最大访问数归一化**（跨池可比）；
- 冷库热度增量（检索命中）节流折叠回 JSONL（`cold_fold_interval_seconds`，默认 30s），重启不丢。

**容量治理**（`capacity_threshold` + `governance_headroom`）：

```
触发：total > capacity + headroom     # headroom = max(10, min(容量/10, 200))，0 可显式覆盖
步骤：先蒸馏（_distill 可覆写，默认占位）→ 淘汰最低热度至容量
```

治理余量让超容后的淘汰**批量发生**，而非每次 insert 全量治理（2 万压测：307.8s → 3.4s，90×）。

**磁盘配额**（`storage.max_bytes`，0 = 不限制）：

```
超 warn_ratio(0.8)·max → 节流告警一次
超 max_bytes → 按热度淘汰冷库，回落到 hard_ratio(0.85)·max
振荡带 [hard, max] 是刻意设计：触发于 ~max，回落于 hard
```

向量字节用**投影值**（`len(store)×384×4`）：向量是节流落盘的，读磁盘文件会低估真实占用，配额保护的是"最终要写盘的量"。

## 存储格式与持久化语义

- 单条记忆 ≈ 文本 JSONL ~250B + 向量 1536B ≈ **1.8KB**（10 万条 ≈ 180MB）；
- `item_id = md5(content)[:16]`：重复内容共享向量、跨库去重；
- 原子写：`np.save` → `.tmp` + `os.replace`；冷区单行追加；
- 向量**节流落盘**（默认 5s）+ `flush()` 强制：崩溃最多丢一个节流窗口的向量，检索时按内容**懒重建**；
- 重启语义：热区不落盘（设计如此）；温区随治理写盘；冷区热度折叠后持久；孤儿向量启动清理。

## 并发与性能模型（面向 2020 前后消费级 i5/i7，6C/12T ~ 8C/16T）

| 环节 | 做法 | 实测 |
|---|---|---|
| 嵌入 | 锁外执行 + 合批窗口（`embed_batch_window_ms`，真实模型建议 4-8ms） | 8 路并发检索 5.4× |
| 打分 | 整池一次 BLAS 矩阵乘 + 位图 Jaccard 一次向量化（**不要把 numpy 调用套进 Python 循环**） | 2000 条 4ms/查询 |
| 治理 | headroom 批量淘汰，向量/温存节流落盘 | 超容治理 90× |
| `stats()` | 缓存计数，O(1) | 21ms → ~0ms |
| 线程 | `torch.set_num_threads(物理核数)`；Python 侧打分**不要加线程池**（GIL） | — |

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

## 自己装上用（3 步，实测链路）

```bash
# 1. 装包（含 coral-sidecar 命令）
pip install coral-memory            # 或本地: pip install dist\coral_memory-0.1.0-py3-none-any.whl

# 2. 起 Sidecar（把 @register_tool 工具桥接成 HTTP 服务，Ctrl+C 退出）
coral-sidecar --port 8765

# 3. 在 DSH Harness 里让 Agent 注册插件（二选一）：
#    a) 告诉 Agent："读 dist/coral_plugin.js，用 cordis_define 注册这个插件"
#    b) 把 build_dsh_cordis_plugin_js("http://127.0.0.1:8765/rpc") 的输出直接喂给 cordis_define
#    注册后 Agent 就能直接调 memory_search / memory_insert 两个工具
```

也可以不用 DSH，任何前端都能 POST 到 Sidecar：

```bash
curl -X POST http://127.0.0.1:8765/rpc \
  -H "content-type: application/json" \
  -d '{"tool":"memory_insert","args":{"content":"你好珊瑚","importance":0.5}}'
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

## API 参考

| 方法 | 签名 | 说明 |
|---|---|---|
| `insert` | `(content, importance=0.0) → MemoryItem \| None` | 重复返回 None 并合并访问统计 |
| `search` | `(query, top_k=None) → List[SearchHit]` | `SearchHit.item/.score/.scores{vector,jaccard,time}` |
| `mark_important` | `(item_id, importance=1.0) → bool` | 显式重要性（热度权重 0.3），冷库也可标记 |
| `reload_config` | `(force=False) → cfg` | 热重载；不传 force 时由 mtime 检测触发 |
| `flush` | `()` | 强制落盘：冷库热度 + 温存 + 向量 |
| `disk_usage` | `() → dict` | 磁盘明细（含配额比例），配额的"账单"接口 |
| `stats` | `() → dict` | hot/warm/cold/total/vectors（O(1)） |
| `fuse_check` | `(items) → bool` | token 熔断（沿用旧版语义） |
| `_distill` | `(cluster) → MemoryItem \| None` | **可覆写**的 LLM 蒸馏接口，默认占位返回 None |
| `@register_tool` | 装饰器 | 注册 `memory_search(query, top_k)` / `memory_insert(content, importance)` |
| `build_dsh_cordis_plugin_js` | `(sidecar_url) → str` | 生成 DSH `harness.registerTool` 插件 JS（含 @Ne 水印） |
| `MemoryToolSidecar` | `(host, port)` | 极简 HTTP 桥：JS `execute` → `POST /rpc` → Python 注册表 |
| `get_coral` | `(config_path) → ThreeDogCoral` | 全局单例（与 Agent 工具共享同一份记忆） |

## 实测基准

> 全部数字来自**合成语料 + hash 嵌入 + 固定 seed** 的确定性基准（本机 8C/16T）。
> 意义：证明能力与上限；不等于真实场景的预期效果。真实项目请用自有数据复测。

| 项目 | 结果（基准条件下） |
|---|---|
| 200 轮对话压测（翻译助手 + 20 轮冷却期画像） | 9 次画像、冷却期严格生效、容量精确收敛 |
| 跨项目共享 | 共享+亲缘度 recall/precision 双 1.0；冷启动 0%→100% |
| 2 万次暴力压测 | 写入 2 万条 88.8s、超容治理 90× 加速、重启一致 ✅ |
| 并行（8C/16T） | 嵌入合批 3.9×、位图 Jaccard 稳态 20×、8 路并发检索 5.4× |

## 适用边界 —— 什么时候它可能变成"负优化"

1. **跨项目无配额共享**：不同领域项目混池 → Top-5 被噪声挤占（precision 1.0 → 0.77 实测）。请用"共享池 + project 亲缘度 + 每项目配额"。
2. **无脑注入上下文**：相关记忆超过 ~5-10 条后边际收益为负。
3. **用 hash 嵌入冒充语义检索**：哈希嵌入只有词面重合，生产请装 sentence-transformers。
4. **小池子激进淘汰**：容量设太低 → recall 塌方。
5. **静默配置回退**：配置文件路径错误会回退默认，部署时请校验路径。

## DSH Harness 集成

```python
from three_dog_coral import build_dsh_cordis_plugin_js, MemoryToolSidecar

js = build_dsh_cordis_plugin_js("http://127.0.0.1:8765/rpc")  # 插件源码（头部含 @Ne 水印注释）
sidecar = MemoryToolSidecar(port=8765)
sidecar.start()   # JS 的 execute 通过 HTTP 桥回 Python 注册表
# 把 js 交给 Agent 的 cordis_define（或写入 cordis.yml）即可注册 memory_search / memory_insert
```

## License

MIT（见 [LICENSE](LICENSE)）。二次开发请在代码中保留 `__author__ = "@Ne"` 与插件水印 🌱
