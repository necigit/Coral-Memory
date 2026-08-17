# 🧠 DLC · 大工程协同（big-project-coordinator）

> 脑珊瑚的**指挥官模式** agent 预设（DLC 扩展包）：把一个大工程拆成多条独立子任务，
> 派给多个子聊天并行推进，全部进度写进脑珊瑚推理线索链路（thread）——任何会话
> `thread_status` 即可接手，跨聊天协作不打架。

## 装什么

| 文件 | 说明 |
|---|---|
| `preset.yml` | 预设元数据（名称 / 描述 / 排序） |
| `agent.cordis.yml` | agent 组合：指挥官 persona + 协同纪律 + 工具集（subagent / thread / goal / plan） |

## 怎么装（DSH Harness）

把整个 `big-project-coordinator/` 目录放到 DSH 的 agent 预设目录：

```bash
# Windows
copy big-project-coordinator %USERPROFILE%\.dsh\.agent-presets\ -Recurse

# macOS / Linux
cp -r big-project-coordinator ~/.dsh/.agent-presets/
```

重启 DSH（或重开会话）后，新会话即可选择「大工程协同」预设。
需要 `mcp__coral__thread_*` 工具（脑珊瑚 MCP 已注册时自动可用）。

## 它做什么

开场白：**"Hi，有什么大工程要我解决吗？"**

1. **分工强度校准**（先看家底再拆活）：`memory_search` 查「用户资源与成本画像」+
   读 `$DSH_HOME/balance.json` 看主模型余额；主模型余额充足 → 放开并行（5~8 子任务），
   外部 API / 视觉 / 生图类子任务一律保守（并发 ≤2），余额低 → 全链降并发。
   分工预算写进 thread 摘要，接手者知道为什么这么拆。
2. 用户点名项目 → `thread_create` 建链路（宏观路径：目标 → 拆解）。
3. 拆成 3+ 条边界清晰的子任务，派给后台 subagent 并行推进，每个子代理：
   先 `thread_status` 看全局（先查后做、不重造轮子）→ 干活 → `thread_advance` 写回（by=自己）。
4. 协调者（本预设）是**唯一轮询者**：子任务卡住 → `thread_interrupt` 后重新派发或自己接管。
5. 收官：推进链路汇总结论 → `thread_archive`（永不遗忘，只是移出活跃总览）。

## 协同纪律（写给每个子代理的边界契约）

- **A. 边界契约**：每个子任务写明 输入/输出/命名约定/落盘位置，写进 thread 摘要，边界不重叠
- **B. 先查后做**：开工前先 `thread_status`，已有产出直接复用
- **C. 单向写回**：子代理之间不互相轮询，只在自己那步 `thread_advance` 写回
- **D. 认领防重**：`by=<谁>` 即认领标记，别人已认领/完成的步骤不重复做
- **E. 对不齐防抖**：共享约定开工前写进 thread，子任务遇歧义先查 thread，查不到再问协调者

小任务不建 thread（保持低噪音）；协调者负责拆解与派发，不默默包办所有子任务。

## 依赖

- 脑珊瑚（本仓库）MCP 注册：`mcp__coral__thread_*` / `mcp__coral__memory_search`
- DSH 子代理工具：`subagent` / `subagent_fork`
- 可选：`$DSH_HOME/balance.json`（主模型余额，读不到会请主会话提供）
