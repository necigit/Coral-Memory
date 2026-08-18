# 脑珊瑚 Coral Memory —— DeepSeek Harness 插件（webui/）

一条命令装齐 **16 个 MCP 记忆工具 + GUI 设置面板**（作者：@Ne，全部原创，不包含第三方源码）。

- **MCP 工具**（`mcp__coral__*`）：`memory_search` / `memory_insert` / `memory_flush` / `memory_delete`
  推理线索链路 `thread_create/status/advance/interrupt/archive/resume/link`
  配置管理 `coral_stats` / `coral_config_get/set/reset` / `coral_report`
- **GUI 面板**（设置 → 「脑珊瑚 Coral」）：
  - **窗口A 统计**：缓存占用（hot/warm/cold/threads/磁盘）、记忆按天分布（近 14 天）、
    即将被淘汰的冷记忆 Top5（最低热度）、缓存文件地址
  - **窗口B 设置**：容量上限 / 检索条数 / 最低分数 / 去重阈值 / 磁盘配额 / 热区保留时长 / 时间衰减
    - 点「保存」才弹确认（列出新旧值），取消则不应用
    - 「恢复默认」二次确认，**只重置配置、不清空任何缓存文件**
    - 嵌入模型只读展示（换模型必须用 `migrate_bge.py`）

## 架构

```
Agent（MCP 工具）            浏览器设置页（lib/client.js, settings.section「脑珊瑚 Coral」）
   ↓ @deepseek-ai/dsh-mcp-client            ↓ 同源 fetch POST /_dsh/coral/api {action, args}
   ↓ stdio 拉起 runtime/coral_mcp_server.py  host 插件（lib/index.js, webServer 同源路由）
                                                    ↓ spawn python
                                            webui/bridge.py —— report / config_get / config_set / config_reset
                                                    ↓（config_set/reset 原子写回 coral_config.json → coral 2s mtime 热加载生效）
```

- **纯 JS 零构建**：`lib/` 即产物（`__ModuleLoader__` 闭包格式，与 DSH 客户端插件约定一致）
- **Python 运行时随包分发**：`runtime/` 内置 `three_dog_coral.py` + `coral_mcp_server.py`
  （`prepack` 自动从仓库根同步最新版本，见 `scripts/sync-runtime.mjs`）
- **零配置安装**：host 插件 provide `coralPaths` 服务，按上文「注册 MCP 工具」的 `mcp-coral` 行
  通过 `!!js` 表达式动态读取 Python 解释器 / server 路径 / 数据目录，不写死任何路径
- 不改 DSH 源码（DeepSeek Harness 全程只读）

## 安装

```bash
# 从 npm（发布后）
dsh plugin --profile web add coral-memory

# 或本地目录 / tarball / GitHub
dsh plugin --profile web add ./webui
dsh plugin --profile web add ./coral-memory-0.2.0.tgz
dsh plugin --profile web add github:yourname/coral-memory

# 重启 dsh web + 刷新页面 → 设置里出现「脑珊瑚 Coral」
```

安装后 GUI 设置面板立即可用；**MCP 记忆工具需注册一次**（见下，一行命令搞定）。

### 注册 MCP 工具（memory_search 等 16 个）

**推荐：幂等 setup 命令**（自动检测、不会重复添加、自动备份）：

```bash
# 在 DSH profile 目录下（含 node_modules/coral-memory 的地方）
node node_modules/coral-memory/lib/setup.mjs
# 或从包内直接跑（npm 安装时）
node ./node_modules/coral-memory/lib/setup.mjs --profile web
```

- 自动定位 `$DSH_HOME/profiles/<profile>/cordis.patch.yml`
- **已存在 coral 的 MCP 注册（serverName: coral / id: mcp-coral）→ 跳过，绝不重复添加**（防 `duplicate loader entry id` 崩溃）
- 未注册 → 备份原文件（`.bak-时间戳`）后追加**动态版**配置（路径由 `coralPaths` 服务提供，零绝对路径）
- `--dry-run` 只预览不写入；可重复运行，幂等

**手动方式**（备选）：在 `$DSH_HOME/profiles/<profile>/cordis.patch.yml` 里追加：

```yaml
- insert:
    - id: mcp-coral
      name: '@deepseek-ai/dsh-mcp-client'
      inject: [coralPaths]
      config:
        serverName: coral
        transport: stdio
        command: !!js ctx.coralPaths.pythonCmd
        args: !!js ctx.coralPaths.pythonArgs
        env:
          CORAL_DATA_DIR: !!js ctx.coralPaths.dataDir
        toolCallTimeoutMs: 120000
```

- `inject: [coralPaths]` 表示本行依赖插件提供的 `coralPaths` 服务（Python 解释器 / server 路径 / 数据目录），
  `!!js` 表达式在服务注入完成后求值——**全程无需填任何 Python / 脚本绝对路径**
- 保存后 DSH 热加载生效（无需重启）；新会话即可用 `mcp__coral__*` 工具

> ⚠️ **重复注册会崩溃——请先检查再添加**：如果 `cordis.patch.yml` 里**已经存在 `mcp-coral` 行**（例如旧版教程、或你手动配过），**不要再加第二行**。
> 顶层列表里重复的 entry id 会让 dsh web 启动失败（`duplicate loader entry id: mcp-coral`），同一 `serverName` 也会被拒载
> （`serverName "coral" is already in use`）。已存在时直接沿用旧行即可，或把旧行删掉换成上面这版。
> 排查：`dsh --dump-config` 可查看实际生效的条目列表。

### 环境变量（可选，均有合理默认）

| 变量 | 默认 | 说明 |
|---|---|---|
| `DSH_CORAL_PYTHON` | 自动探测 | 显式指定 Python 解释器（venv / 绝对路径） |
| `DSH_CORAL_DATA_DIR` | `$DSH_HOME/coral` | 记忆数据目录（coral_config.json + memory_data/） |
| `DSH_CORAL_AUTO_INSTALL` | 关 | `1` 时缺 numpy/sentence-transformers 自动 pip install |

首次激活会自动探测 Python 与依赖：缺 numpy 给出安装提示（或自动装），缺
sentence-transformers 降级 hash 嵌入并提示（中文语义检索建议装 bge 模型，见仓库 README）。

## 卸载

```bash
dsh plugin --profile web remove coral-memory
```

## 界面

| 任务栏演示 | 统计窗口 | 设置窗口 |
|---|---|---|
| ![任务栏演示](https://raw.githubusercontent.com/necigit/Coral-Memory/main/webui/screenshots/taskbar.png) | ![统计窗口](https://raw.githubusercontent.com/necigit/Coral-Memory/main/webui/screenshots/settings-main.png) | ![设置窗口](https://raw.githubusercontent.com/necigit/Coral-Memory/main/webui/screenshots/settings-config.png) |

## 本地测试

```bash
# bridge（GUI 数据通道）
python -X utf8 webui/bridge.py report                # 审计报告
python -X utf8 webui/bridge.py get_config '{"path":"memory.capacity_threshold"}'
python -X utf8 webui/bridge.py set_config '{"key_path":"retrieval.top_k","value":8}'
python -X utf8 webui/bridge.py reset_config '{}'

# MCP server（模拟 agent 调用）
printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05"}}\n{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n' \
  | python -X utf8 runtime/coral_mcp_server.py

# 打包校验
node scripts/sync-runtime.mjs --check   # runtime/ 与仓库根一致性
npm pack --dry-run                      # 发布内容预览
```
