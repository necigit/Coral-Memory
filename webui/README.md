# 脑珊瑚 GUI 插件（webui/）

DSH 设置面板：管理 coral 记忆缓存（作者：@Ne，全部原创，不包含第三方源码）。

## 功能

- **窗口A 统计**：缓存占用（hot/warm/cold/threads/磁盘）、记忆按天分布（近 14 天）、
  即将被淘汰的冷记忆 Top5（最低热度）、缓存文件地址
- **窗口B 设置**：容量上限 / 检索条数 / 最低分数 / 去重阈值 / 磁盘配额 / 热区保留时长 / 时间衰减
  - 点「保存」才弹确认（列出新旧值），取消则不应用
  - 「恢复默认」二次确认，**只重置配置、不清空任何缓存文件**
  - 嵌入模型只读展示（换模型必须用 `migrate_bge.py`）

## 架构

```
浏览器设置页（lib/client.js, settings.section「脑珊瑚 Coral」）
   ↓ 同源 fetch POST /_dsh/coral/api {action, args}
host 插件（lib/index.js, webServer 同源路由）
   ↓ spawn python
webui/bridge.py —— 复用 three_dog_coral 的 report / config_get / config_set / config_reset
   ↓（config_set/reset 原子写回 coral_config.json → coral 2s mtime 热加载生效）
```

- 纯 JS 零构建：`lib/` 即产物（`__ModuleLoader__` 闭包格式，与 DSH 客户端插件约定一致）
- 不改 DSH 源码（J:\deepseek-harness-master 全程只读）

## 安装（一次性）

```bash
# 1. 装进 web profile（bundle patch 自动注入 coral-gui 行）
dsh plugin --profile web add ./webui

# 2. 重启 dsh web + 刷新页面 → 设置里出现「脑珊瑚 Coral」
# 之后改 lib/client.js 由 HMR 免刷新热更（首次注册必须重启）
```

## 卸载

```bash
dsh plugin --profile web remove coral-gui
```

## 本地测试 bridge

```bash
python -X utf8 webui/bridge.py report                # 审计报告
python -X utf8 webui/bridge.py get_config '{"path":"memory.capacity_threshold"}'
python -X utf8 webui/bridge.py set_config '{"key_path":"retrieval.top_k","value":8}'
python -X utf8 webui/bridge.py reset_config '{}'
```
