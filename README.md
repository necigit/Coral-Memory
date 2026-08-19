# 脑珊瑚 · Coral Memory 🪸

> 给 LLM Agent 加一层**会记事的珊瑚礁**——记住该记的，忘掉该忘的，检索时只捞最相关的。
> 不占上下文窗口，跨会话不丢失。
>
> 你只管跟 AI 聊天，重复交代的事交给它。

**EN**: A heat-aware persistent memory layer for LLM agents — three-tier storage, multi-fusion retrieval, heat-based eviction, reasoning threads for cross-chat collaboration, and a zero-dependency MCP stdio bridge.

---

## 它做了什么

脑珊瑚解决一个问题：**LLM 每次新会话都失忆，用户只能重复交代**。

它在 Agent 和模型之间加了一层记忆：
- Agent 说"记住这个"，记忆存下来
- 下次新会话，Agent 搜索相关记忆注入上下文
- 旧记忆按热度自动淘汰，不用手动清理

不是 RAG，不是向量数据库，不是缓存插件。它不产生答案，只负责"记得"。

**为什么叫脑珊瑚**：记忆像珊瑚礁一样，常被触碰的部分越活越旺（热度高、难淘汰），没人理的部分慢慢钙化沉底（自动降级）。它在自己长，你不用管。

---

## GUI 一览（DSH Harness 插件）

| 任务栏 | 统计窗口 | 设置窗口 |
|---|---|---|
| ![任务栏](https://raw.githubusercontent.com/necigit/Coral-Memory/main/webui/screenshots/taskbar.png) | ![统计](https://raw.githubusercontent.com/necigit/Coral-Memory/main/webui/screenshots/settings-main.png) | ![设置](https://raw.githubusercontent.com/necigit/Coral-Memory/main/webui/screenshots/settings-config.png) |

- **任务栏**：推理线索链路看板——活跃链路标题 / 状态 / 步数 / 最近推进者
- **统计窗口**：缓存占用 + 记忆按天分布 + 即将被淘汰的冷记忆 Top5
- **设置窗口**：容量 / 检索条数 / 最低分数 / 磁盘配额 / 热区保留时长，热加载即时生效

---

## 快速开始

```bash
pip install numpy
```

```python
import asyncio
from three_dog_coral import ThreeDogCoral

async def main():
    coral = ThreeDogCoral("coral_config.json")
    await coral.insert("用户喜欢喝冰镇拿铁咖啡", importance=0.7)
    hits = await coral.search("咖啡偏好", top_k=5)
    for h in hits:
        print(h.score, h.content, h.scores)

asyncio.run(main())
```

---

## 集成到 DSH / 其他客户端

### MCP 方式（推荐）

零依赖，手写 MCP stdio 协议，不依赖 pip mcp 包。

```bash
# 1. 编辑 $DSH_HOME/profiles/<profile>/cordis.patch.yml，加一条：
#    - insert:
#        - id: mcp-coral
#          name: '@deepseek-ai/dsh-mcp-client'
#          inject: [coralPaths]
#          config:
#            serverName: coral
#            transport: stdio
#            command: !!js ctx.coralPaths.pythonCmd
#            args: !!js ctx.coralPaths.pythonArgs
#            env:
#              CORAL_DATA_DIR: !!js ctx.coralPaths.dataDir
#            toolCallTimeoutMs: 120000
#
#    ⚠️ 不要重复添加同名 entry——会导致 dsh web 启动崩溃

# 2. 保存即生效（DSH 有 HMR）
# 3. 新会话自动获得全套工具
```

可用工具：`memory_search` / `memory_insert` / `memory_flush` / `memory_delete` + `thread_*`（链路协作）+ `coral_config_*`（配置管理）

> **持久化提醒**：新记忆先进内存热区，重启后丢失。写重要记忆后务必调 `memory_flush` 落盘。

### HTTP Sidecar 方式

```bash
curl -X POST http://127.0.0.1:8765/rpc \
  -H "content-type: application/json" \
  -d '{"tool":"memory_insert","args":{"content":"你好珊瑚","importance":0.5}}'
```

---

## 核心功能

### 三级存储 + 热度淘汰

```
插入 → 热区（内存，最快） → TTL过期 → 温区（内存+JSON持久化） → 超容 → 冷区（JSONL追加）
```

- 热区：最新最热的记忆，检索最快
- 温区：写盘持久化，热度统计随写盘保留
- 冷区：尾部流式读，不读全文件
- 常被回忆的记忆热度越高，越难被淘汰——越用越"记得住"

### 多路融合检索

三路打分，加权融合：

```
score = 0.6·向量相似度 + 0.2·文本重合 + 0.2·时间新鲜度
```

- 向量：BLAS 矩阵乘，2000 条 ~2ms
- Jaccard：2048-bit 哈希位图向量化，稳态 12-13x 加速
- 时间衰减：最近说过的优先

### 推理线索链路（Thread）

**永不遗忘的跨聊天协作机制。** 普通记忆按热度淘汰，链路不参与任何淘汰——项目进度不会被"忘掉"。

```text
聊天A: thread_create("发布 v2.0", "目标：升级嵌入模型", by="聊天A")
聊天B: thread_status                          # 进来就看到全局
聊天B: thread_advance(<id>, "migrate_bge.py 跑通", done=True, by="聊天B")
聊天C: thread_advance(<id>, "向量重建完成", by="聊天C")
聊天A: thread_archive(<id>)                   # 归档，永不遗忘
```

多进程一致性：锁文件串行化写者 + 文件指纹检测外部变更 + 按 step_id 合并。
压测：3 进程并发各推 30 步，90/90 零丢失。

### 配置热加载

在聊天里直接改配置，不用重启：

```text
coral_config_set memory.capacity_threshold 2000   # 容量调大
coral_config_get retrieval.weights                 # 查检索权重
coral_stats()                                      # 看占用
```

### LLM 蒸馏

相似记忆簇交给 LLM 压缩成摘要（配置 `llm` 段即启用）。未配置时优雅降级为"不蒸馏"，不阻断治理。

### 磁盘配额

```text
超 80% → 节流告警
超 100% → 按热度淘汰冷库，回落到 85%
```

向量用投影值计算（防止节流落盘导致低估真实占用）。

---

## 嵌入模型

模型不随仓库分发，首次运行自动从 HuggingFace 下载。

| 模型 | 维度 | 中文 | 说明 |
|---|---|---|---|
| `all-MiniLM-L6-v2`（默认） | 384 | 一般 | 小快，纯本地 |
| `BAAI/bge-small-zh-v1.5`（推荐） | 512 | 更准 | ~95MB，缓存在 `.cache/huggingface` |

换模型后需重建向量：

```bash
python migrate_bge.py   # 修订+重嵌入+重建+验证，一步到位
```

---

## DLC · 大工程协同

仓库自带指挥官 agent 预设 [`dlc/big-project-coordinator/`](dlc/big-project-coordinator/README.md)：

开场"Hi，有什么大工程要我解决吗？"→ 自动建链路 → 拆子任务 → 派多个子聊天并行推进 → 写回进度 → 任何会话可接手。

---

## 配置参考

> 配置不入库（可能含 API key）。首次运行缺文件时自动生成默认配置。
> 模板：[`coral_config.example.json`](coral_config.example.json)

| 段 | 关键项 | 默认 | 说明 |
|---|---|---|---|
| `memory` | `capacity_threshold` | 1000 | 记忆总数上限 |
| | `hot_ttl_hours` / `max_hot_entries` | 24 / 50 | 热区参数 |
| `retrieval` | `weights` | 0.6/0.2/0.2 | 向量/Jaccard/时间 |
| | `top_k` / `min_score` | 3 / 0.35 | 检索参数 |
| `heat` | `weights` | 0.4/0.3/0.3 | 频率/最近访问/重要性 |
| `storage` | `max_bytes` | 0（不限） | 磁盘配额 |
| `threads` | `path` | `memory_data/coral_threads.json` | 链路存储（永不遗忘） |
| `llm` | `base_url` / `api_key` | 空 | 蒸馏端点（OpenAI 兼容） |

完整配置见 [`coral_config.example.json`](coral_config.example.json)。

---

## 实测数据

> 合成语料 + hash 嵌入 + 固定 seed 的确定性基准（8C/16T）。
> **能力上限演示，不是典型场景预期。** 脚本在 `benchmarks/`、`tests/`、`stress/`，可复现。

| 项目 | 结果 |
|---|---|
| 2 万次写入 | 83.7s，检索 12ms/次 |
| 超容治理 | 批量淘汰，90x 加速 |
| 并行（8C/16T） | 嵌入合批 3.9-6.8x，Jaccard 12-13x |

---

## 适用边界

诚实地说，这些场景它可能帮不上忙：

1. **多领域混用同一池** → 不相关高频记忆挤占 Top-K。按项目拆独立实例解决。
2. **无脑注入太多条** → 超过 5-10 条边际收益为负。默认 top_k=3 就够。
3. **用 hash 嵌入冒充语义** → 只有词面重合。生产请装 sentence-transformers。
4. **容量设太低** → recall 塌方。留够余量。
5. **配置路径写错** → 静默回退默认值，部署时请校验。
6. **基准数字当承诺** → 真实效果取决于嵌入模型、语料、查询措辞。用自有数据复测。

---

## 存储细节

- 单条记忆：文本 ~250B + 向量 ~1.5KB ≈ **1.8KB**（10 万条 ≈ 180MB）
- `item_id = md5(content)[:16]`：重复内容共享向量、跨库去重
- 向量节流落盘（默认 5s）+ `flush()` 强制；崩溃最多丢一个节流窗口
- 重启语义：热区不落盘（设计如此）；温区随治理写盘；孤儿向量启动清理

---

## API 参考

| 方法 | 说明 |
|---|---|
| `insert(content, importance)` | 插入记忆；重复返回 None 并合并访问统计 |
| `search(query, top_k)` | 检索；返回 SearchHit（含分项得分） |
| `mark_important(item_id)` | 显式标记重要性 |
| `delete(item_id)` | 按 ID 彻底删除 |
| `flush()` | 强制落盘 |
| `stats()` | O(1) 统计 |
| `reload_config()` | 热重载配置 |
| `disk_usage()` | 磁盘明细 |
| `thread_create / status / advance / interrupt / archive / resume / link` | 推理线索链路 |
| `config_get / config_set` | 配置管理 |

MCP 工具前缀：`mcp__coral__`

---

## License

MIT（见 [LICENSE](LICENSE)）。

**唯一的要求**：二次开发或发布衍生版本时，请保留作者署名（`@Ne` · 751286928@qq.com）与代码中的 `__author__`、插件水印。写代码的人只想被记得 🌱
