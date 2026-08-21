# DSH 插件（plugins/）

本目录是脑珊瑚（Coral Memory）系统配套的 DeepSeek Harness 客户端插件。

## ⚠️ 新版本安装/注册方式有变化（重要）

升级 DSH 到新版本后，**客户端插件的注册方式变了**，请务必按下面做，否则插件不生效（Remote 端点返回 404）：

| 插件 | 类型 | 注册方式 |
|---|---|---|
| `coral-board` | dual-face 客户端插件（只有 `dsh.client`，**无** `dsh.bundle`） | **不能**放进 `dsh.profile.bundles`，只能通过 profile 的 `cordis.patch.yml` 用 `insert` 注册成 `dsh.client` 行 |
| `ui-balance` | dual-face 客户端插件（同上） | 同上 |

关键规则：

1. **`dsh.profile.bundles` 只用于带 `dsh.bundle` 补丁的包**。
   把只有 `dsh.client` 的客户端插件放进 bundles，新版本会直接报错：
   `profile bundle "@deepseek-ai/dsh-client-coral-board" declares no dsh.bundle in its package.json`。

2. **Remote 端点必须有 `./typert` 宿主清单**（`lib/typert.host.js`）。
   否则 `/api/board/*`、`/api/balance/*` 返回 **HTTP 404**。本仓库两个插件均已导出 `./typert`，
   由 `typert-loader` 自动注册进网关；新增 `@Remote` 方法时需同步更新该清单。

3. **profile 依赖建议用 `link:`（symlink）指向本仓库源码**，改完即时生效。
   `file:` 会在 `~/.dsh/profiles/<name>/node_modules` 留一份拷贝，改了源码不会自动同步。

## profile 注册示例

```yaml
# ~/.dsh/profiles/<name>/cordis.patch.yml
- insert:
    - id: coral-board
      name: '@deepseek-ai/dsh-client-coral-board'
    - id: ui-balance
      name: '@deepseek-ai/dsh-client-ui-balance'
```

```jsonc
// ~/.dsh/profiles/<name>/package.json
"dependencies": {
  "@deepseek-ai/dsh-client-coral-board": "link:<your-dsh-path>/packages/client/coral-board",
  "@deepseek-ai/dsh-client-ui-balance": "link:<your-dsh-path>/packages/client/ui-balance"
}
```

## 各插件

- [coral-board](coral-board/README.md)：推理线索任务板，只读投影 `coral_threads.json`，零模型 token。
- [ui-balance](ui-balance/README.md)：API 余额胶囊，node 半部查询各 provider 余额端点，零模型 token。
