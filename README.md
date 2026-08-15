# 脑珊瑚 · Coral Memory (Brain Coral)

> **A heat-aware persistent memory layer for LLM agents** — 面向 LLM Agent 的记忆层中间件：
> 三级存储（热/温/冷）、多路融合检索、热度生命周期淘汰、配置热加载、推理线索链路（永不遗忘的跨聊天协作）、DSH Harness 插件集成。
>
> *Origin: started as a ComfyUI prompt-manager idea, grew into a memory layer. 本来只想管提示词，结果长成了一片珊瑚礁。*
>
> **EN**: Coral Memory is a heat-aware persistent memory layer for LLM agents —
> three-tier storage (hot/warm/cold), multi-fusion retrieval, heat-based lifecycle eviction,
> config hot-reload, and a zero-dependency MCP stdio bridge so any MCP-capable client
> (DSH Harness, Claude Desktop, Cline, ...) gets `memory_search` / `memory_insert` tools.
> Embedding models are fetched on first run from HuggingFace — never committed to the repo.

---

## Author — Mr. Code Muggle (@Ne)

Mr. Code Muggle — hi guys, I made something fun to play with: fork it, break it, rebuild it — just maybe mention me (lol).
The coral remembers what I can't. Questions? 📮 751286928@qq.com
Shoutout to every open-source maker out there 🌱

---

## 这是什么 / 不是什么

**是什么**：给 LLM Agent 用的"记忆层"——记住该记住的（你的偏好、说过的话），
忘掉该忘掉的（没用的旧记忆，按热度淘汰），并在需要时只捞最相关的几条给你。
不占上下文窗口，跨会话不丢失。

**不是什么**：
- 不是 RAG 框架（不负责分块、文档摄取、生成）；
- 不是向量数据库（无 ANN 索引，单机 ~20 万条以内的内存全量打分）；
- 不是缓存插件（缓存是"别重复计算"，它是"别重复交代"）；
- 它不产生答案，它只负责"记得"——所以单独看它确实看不出名堂，
  接上应用（翻译助手 / 客服 / Agent）才显现价值。

**起源**：最初只是想管理 ComfyUI 的提示词，做着做着发现
"提示词管理"的本质是"该记住什么、该忘掉什么、该在什么场景召回什么"，
越想越离谱，最后长成了一个记忆系统。

---

## 对用户的价值（玩家视角）

**"它让 AI 记住你的习惯和说过的话，每次只挑最相关的几条回忆出来用——不用你重复交代，也不用把整个聊天记录塞给 AI。"**

| 玩家问题 | 答案（标注前提与来源） |
|---|---|
| **能提升命中率吗？** | **确定性基准下能，真实场景不承诺**。合成语料 + hash 嵌入 + 固定 seed 的基准里：冷启动 0%→100% 有结果、recall@5 与 precision@5 均达 1.0——这是**能力上限演示**，不是典型预期。真实语料/真实模型下命中率大概率更低，随语料分布与模型质量波动；接入前请用自有数据复测（`benchmarks/bench_cross_project.py` 可替换语料重跑） |
| **能省上下文吗？** | **理论可行，实测不好说**。省多少取决于用法——前提是把"全量会话历史"替换为"Top-5 相关记忆"注入；但 HARNESS 太强了测不出稳定的效果，大概可能有效哈哈。前提不满足则没有任何节省 |
| **会越用越卡吗？** | **默认配置下不会**。热度淘汰自动清理低热度旧记忆；实测 2 万条写入 83.7s、检索 12ms/次、stats()≈0ms（本机 8C/16T，hash 嵌入，`stress/stress_20k.py` 可复现）。但延迟随池规模上升：容量调大、单条记忆变长都会变慢——这是**无 ANN 索引的全量打分检索**的固有特性 |
| **要重新说一遍吗？** | **多数情况下不用**。文本相似度达到阈值（默认 Jaccard ≥ 0.7）时重复偏好自动合并，命中过的记忆热度更高、更难淘汰（200 轮压测实测去重 18 次）。但**换种说法或细节不同就不会合并**，会并存为两条——它不是语义级去重 |

> ⚠️ **预期管理（写给集成者，不夸张承诺）**：本表数字全部来自仓库内**确定性基准**（合成语料、hash 嵌入、固定 seed），
> 脚本已随仓库发布（`benchmarks/`、`tests/`、`stress/`），可自行复现。
> 它们证明的是**能力上限**，不是任何真实场景的承诺。真实效果取决于：嵌入模型
> （生产默认 `BAAI/bge-small-zh-v1.5`，中文比基准用的 hash 嵌入更准，但仍是向量相似度、不是语义理解）、
> 语料分布、查询措辞、应用如何注入记忆。**模糊召回是概率性的：测试语料越接近你的真实数据，
> 数字才越有参考价值；记忆池小、语料杂、模型弱时，命中率可能远低于基准。**
> 记忆层不产生答案、不保证命中——它只提供"记住"的机制。

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
- Jaccard：2048-bit 哈希位图 + `numpy.bitwise_count` 一次向量化，位图跨查询缓存（基准下稳态 **12-13×** 加速，2000~2 万条实测）；
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

向量字节用**投影值**（`len(store)×dim×4`，dim 为嵌入模型维度）：向量是节流落盘的，读磁盘文件会低估真实占用，配额保护的是"最终要写盘的量"。

## 嵌入模型（模型不随仓库分发，按需自取）

| 模型 | 维度 | 中文语义 | 说明 |
|---|---|---|---|
| `sentence-transformers/all-MiniLM-L6-v2`（默认） | 384 | 一般 | 小快；纯本地 |
| `BAAI/bge-small-zh-v1.5`（推荐中文） | 512 | 明显更准 | 首次运行自动从 HuggingFace 下载（~95MB，缓存于用户目录 `.cache/huggingface`），**不入仓库** |

**指路**：[bge-small-zh-v1.5 on HuggingFace](https://huggingface.co/BAAI/bge-small-zh-v1.5) · [all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)

配置：`coral_config.json` 的 `embedding.model_name` / `embedding.dim` 两处，改完重启服务。

**换模型后必须重建向量**（旧维度向量与新维度不匹配会自动丢弃，检索会失去向量分）：

```bash
# 1. 先停掉正在运行的 coral 进程（DSH 会自动重连）
# 2. 修改 coral_config.json 的 model_name / dim
python migrate_bge.py   # 修订+重嵌入+重建向量区+检索验证，一步到位
```

## 推理线索链路（Thread）—— 永不遗忘的跨聊天协作

> **一句话**：聊天 A 说"我要干啥"（宏观路径），聊天 B/C/D/E/F 各自看到并推进——链路把"到哪了"
> 用几句话记死，任何聊天一进来 `thread_status` 就知道全局。

**与普通记忆的本质区别**：普通记忆（热/温/冷池）按热度淘汰、超容治理、磁盘配额；
链路存独立文件 `memory_data/coral_threads.json`，**不参与任何淘汰/治理/配额**——"永不遗忘"。
每条链路 = 标题（短短语）+ 摘要（宏观路径，可不断更新）+ 步骤链（谁在何时推进了什么，
每条步骤带全局唯一 `step_id`）+ 父子链接（线索链串联）。

**多进程一致性**（跨聊天的地基）：
- 变更落盘**节流**（250ms 突发合并，真实操作间隔远大于此，仍即时落盘）+ `flush()`/进程退出强制；
- 写入用**锁文件**（O_EXCL + 10s stale 检测）串行化多进程写者，`os.replace` 带退避重试（防 Windows 撞文件）；
- 写前按文件指纹 (mtime+size) 检测外部变更并**按 `step_id` 合并远端步骤**——
  3 进程并发各推 30 步实测 **90/90 零丢失**（`stress/stress_threads.py` 可复现）；
- 压测：300 链路 × 20 推进 = 6000 操作 **0.08s**（8 万推进/秒），重启加载完全一致。

**跨聊天协作流程**：

```text
聊天A: thread_create("发布 v2.0", "升级嵌入模型并优化检索性能", by="聊天A")
聊天B: thread_status                          # 一进来就看到宏观路径
聊天B: thread_advance(<thread_id>, "migrate_bge.py 已跑通", done=True, by="聊天B")
聊天C: thread_status(thread_id=<thread_id>)   # 看到步骤链，接着推进
聊天C: thread_advance(<thread_id>, "向量重建完成，检索验证通过", by="聊天C")
聊天A: thread_interrupt(<thread_id>, reason="等上游依赖")   # 暂停
聊天A: thread_resume(<thread_id>)             # 恢复
聊天A: thread_archive(<thread_id>)            # 归档（永不遗忘，只是不在活跃总览）
```

**MCP 工具**（DSH 中为 `mcp__coral__thread_*`）：`thread_create` / `thread_status` /
`thread_advance` / `thread_interrupt` / `thread_archive` / `thread_resume` / `thread_link`。
`thread_status` 不带参数返回全部活跃链路总览（含子链路归属），Agent 直接在聊天里渲染成看板。

## 管理上下文缓存（配置工具）

三个 MCP 工具让 Agent/用户在聊天里直接管理记忆层配置（热加载即时生效 + 持久化）：

- `coral_stats()` —— 体检：热/温/冷占用、线程数、磁盘与配额比例；
- `coral_config_get(path?)` —— 查看配置，支持点分路径（如 `memory.capacity_threshold`）；
- `coral_config_set(key_path, value)` —— 改配置并原子写回 `coral_config.json`（重启保持）。
  改 `memory.capacity_threshold` 会立即触发一次容量治理；`retrieval.top_k` / `retrieval.min_score` /
  `storage.max_bytes` 等随改随生效。**受保护路径**：`paths.*` / `threads.*`（改路径会脱离数据目录）、
  `embedding.*`（换模型/维度需 `migrate_bge.py` 重建向量）。

```text
coral_stats()                              # 看占用
coral_config_set memory.capacity_threshold 2000   # 容量调大
coral_config_get retrieval.weights         # 查检索权重
```

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

## 自己装上用（DSH Harness，推荐 MCP 方式，实测链路）

```bash
# 0. 前置：Python 能 import three_dog_coral（本仓库目录即可），无需额外依赖
#    （coral_mcp_server.py 手写 MCP stdio 协议，不依赖 pip mcp 包）

# 1. 编辑 DSH 配置：$DSH_HOME/profiles/<profile>/cordis.patch.yml 加一条：
#    - id: mcp-coral
#      name: '@deepseek-ai/dsh-mcp-client'
#      config:
#        serverName: coral
#        transport: stdio
#        command: C:/Python313/python.exe          # 改成你的 python 路径
#        args: ['./coral_mcp_server.py']
#        toolCallTimeoutMs: 120000                 # 首次调用要加载嵌入模型

# 2. DSH 对 cordis.patch.yml 有 HMR：保存即生效，无需重启
# 3. 开新会话，Agent 直接获得两个工具：
#    mcp__coral__memory_search / mcp__coral__memory_insert
```

不用 DSH 也可以：任何 MCP 客户端（Claude Desktop 等）都能以 stdio 方式连接
`coral_mcp_server.py`，或 POST 到 Sidecar：

```bash
curl -X POST http://127.0.0.1:8765/rpc \
  -H "content-type: application/json" \
  -d '{"tool":"memory_insert","args":{"content":"你好珊瑚","importance":0.5}}'
```

> 关于 `cordis_define`：那是 `@deepseek-ai/dsh-tool-cordis` 插件提供的动态注册工具，
> 默认 web profile 没启用它。**MCP 方式是 DSH 的标准集成路径**（配置一次、HMR 生效、
> 所有会话可用），优先用它；想用 cordis_define 需先在 cordis.patch.yml 里
> `insert` 该插件，再让 Agent 读 `dist/coral_plugin.js` 动态注册。


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
| `threads` | `path` | `memory_data/coral_threads.json` | 推理线索链路存储（永不遗忘，不参与淘汰/治理/配额） |
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
| `@register_tool` | 装饰器 | 注册 `memory_search(query, top_k)` / `memory_insert(content, importance)` / `memory_flush()` |
| `thread_create` | `(title, summary="", parent_thread_id=None, by="") → ThreadItem` | 创建推理线索链路（永不遗忘） |
| `thread_status` | `(thread_id=None, include_archived=False, query=None) → List[ThreadItem]` | 查看链路：无参=活跃总览；指定 ID=详情含步骤链 |
| `thread_advance` | `(thread_id, note, done=False, by="") → ThreadItem` | 推进链路（追加步骤节点），聊天间协作 |
| `thread_interrupt` | `(thread_id, reason="") → ThreadItem` | 中断链路（内容保留，可恢复） |
| `thread_archive` | `(thread_id) → ThreadItem` | 归档链路（不在活跃总览，永不遗忘） |
| `thread_resume` | `(thread_id) → ThreadItem` | 恢复中断/归档的链路 |
| `thread_link` | `(child_id, parent_id) → bool` | 把两条链路串成父子（线索链） |
| `config_get` | `(path=None) → Any` | 查看配置（点分路径；缺省全量） |
| `config_set` | `(key_path, value) → dict` | 改配置：热加载生效 + 原子写回配置文件（管理上下文缓存入口） |
| `memory_flush` | MCP 工具 | **持久化关键**：热区记忆默认只存内存（重启丢失），写重要记忆后调它落盘（温存 + 向量） |
| `build_dsh_cordis_plugin_js` | `(sidecar_url) → str` | 生成 DSH `harness.registerTool` 插件 JS（含 @Ne 水印） |
| `MemoryToolSidecar` | `(host, port)` | 极简 HTTP 桥：JS `execute` → `POST /rpc` → Python 注册表 |
| `get_coral` | `(config_path) → ThreeDogCoral` | 全局单例（与 Agent 工具共享同一份记忆） |

## 实测基准

> 全部数字来自**合成语料 + hash 嵌入 + 固定 seed** 的确定性基准（本机 8C/16T）。
> 定位：**能力上限演示，不是典型场景预期**；真实项目请用自有数据复测。
> 脚本已随仓库发布（`benchmarks/`、`tests/`、`stress/`），可自行复现。

| 项目 | 结果（基准条件下） |
|---|---|
| 200 轮对话压测（翻译助手 + 20 轮冷却期画像） | 9 次画像、冷却期严格生效、容量精确收敛 |
| 跨项目共享 | 共享+亲缘度 recall/precision 双 1.0；冷启动 0%→100% |
| 2 万次暴力压测 | 写入 2 万条 **83.7s**、检索 12ms/次、超容治理批量淘汰、重启一致 ✅ |
| 并行（8C/16T） | 嵌入合批 3.9×~6.8×、位图 Jaccard 稳态 12-13×、8 路并发检索 6.5× |

## 适用边界 —— 什么时候它可能变成"负优化"

1. **跨项目无配额共享**：不同领域项目混池 → Top-5 被噪声挤占（precision 1.0 → 0.77 实测）。请用"共享池 + project 亲缘度 + 每项目配额"。
2. **无脑注入上下文**：相关记忆超过 ~5-10 条后边际收益为负。
3. **用 hash 嵌入冒充语义检索**：哈希嵌入只有词面重合，生产请装 sentence-transformers（中文推荐 bge 系列）。
4. **小池子激进淘汰**：容量设太低 → recall 塌方。
5. **静默配置回退**：配置文件路径错误会回退默认，部署时请校验路径。
6. **把基准数字当承诺**：1.0 级命中率是合成语料 + hash 嵌入下的**上限演示**，不是你的真实预期。接入 LLM 应用前请用自有数据复测，别拿 README 数字对外承诺——测试语料越像你的真实数据，结果才越有参考价值。

## DSH Harness 集成

**推荐：MCP stdio 桥（零依赖）**

```python
# coral_mcp_server.py —— 手写 MCP stdio 协议，把 @register_tool 注册表桥给任意 MCP 客户端
# DSH 侧：cordis.patch.yml 注册 @deepseek-ai/dsh-mcp-client（见"自己装上用"），
#         工具以 mcp__coral__memory_search / mcp__coral__memory_insert / mcp__coral__memory_flush
#         以及 mcp__coral__thread_*（推理线索链路，跨聊天协作）出现
```

> ⚠️ **持久化（重要）**：`memory_insert` 的新记忆先进**内存热区**——进程/重启后丢失（热区不落盘是三级存储的设计）。**写重要记忆后务必调用 `memory_flush`**（把温存 + 向量落盘到 `memory_data/`）。一条建议流程：`memory_insert(内容, importance)` → `memory_flush()`。

**备选：HTTP Sidecar + cordis 插件 JS**

```python
from three_dog_coral import build_dsh_cordis_plugin_js, MemoryToolSidecar

js = build_dsh_cordis_plugin_js("http://127.0.0.1:8765/rpc")  # 插件源码（头部含 @Ne 水印注释）
sidecar = MemoryToolSidecar(port=8765)
sidecar.start()   # JS 的 execute 通过 HTTP 桥回 Python 注册表
# 需要 @deepseek-ai/dsh-tool-cordis 插件提供 cordis_define 才能动态注册；或用 MCP 方式更省事
```

## License

MIT（见 [LICENSE](LICENSE)）。作者：Mr. Code Muggle (@Ne) · 751286928@qq.com。
二次开发请在代码中保留 `__author__`（含 @Ne 标识）与插件水印 🌱
