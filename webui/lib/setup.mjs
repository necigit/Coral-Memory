#!/usr/bin/env node
/**
 * coral-memory 幂等 MCP 注册脚本（部署安全）
 *
 * 用法（装完 coral-memory 插件后）：
 *   node node_modules/coral-memory/lib/setup.mjs                 # 自动定位 profile
 *   node setup.mjs --profile <name>                              # 指定 profile
 *   node setup.mjs --dry-run                                     # 只检测不写入
 *
 * 行为（幂等，可反复运行）：
 *   1. 定位 DSH profile 的 cordis.patch.yml
 *   2. 检测是否已存在 coral 的 MCP 注册（serverName: coral 或 id: mcp-coral）
 *      —— 已存在 → 跳过，不重复添加（防 duplicate loader entry id 崩溃）
 *   3. 不存在 → 备份原文件后追加动态版 mcp-coral 配置（零绝对路径）
 *
 * 只改动目标 cordis.patch.yml，不碰任何其他文件。
 */
import { existsSync, readFileSync, writeFileSync, copyFileSync } from 'node:fs'
import { join, dirname } from 'node:path'
import { homedir } from 'node:os'
import { fileURLToPath } from 'node:url'

const PKG_ROOT = dirname(dirname(fileURLToPath(import.meta.url)))

// ── 检测锚点：存在任一即视为「已注册」──────────────────────────────
const ANCHORS = [
  /^\s*id:\s*mcp-coral\s*$/m,          // entry id
  /^\s*serverName:\s*coral\s*$/m,      // mcp-client serverName
]

// 动态版注册配置（与 v0.2.0 验证过的格式一致；路径由 coralPaths 服务提供）
const MCP_INSERT = `# ── coral-memory MCP 工具（setup.mjs 幂等追加，重复运行不会重复添加）──
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
`

function log(msg) {
  process.stdout.write(`[coral-memory setup] ${msg}\n`)
}

function resolveProfilePatch(profileName) {
  const dshHome = process.env.DSH_HOME || join(homedir(), '.dsh')
  const candidates = [
    join(dshHome, 'profiles', profileName, 'cordis.patch.yml'),
    join(dshHome, 'profiles', 'cordis.patch.yml'),
  ]
  return candidates.find((p) => existsSync(p))
}

function hasCoralMcp(content) {
  return ANCHORS.some((re) => re.test(content))
}

function main() {
  const args = process.argv.slice(2)
  const profileIdx = args.indexOf('--profile')
  const profileName = profileIdx >= 0 && args[profileIdx + 1] ? args[profileIdx + 1] : 'web'
  const dryRun = args.includes('--dry-run')

  const patchPath = resolveProfilePatch(profileName)
  if (!patchPath) {
    log(`未找到 DSH profile patch 文件（尝试过 ${join(homedir(), '.dsh', 'profiles', profileName, 'cordis.patch.yml')}）`)
    log('请确认 DSH 已初始化（$DSH_HOME/profiles/<profile>/cordis.patch.yml 存在），或用 --profile 指定')
    process.exit(1)
  }
  log(`profile patch: ${patchPath}`)

  let content = ''
  try {
    content = readFileSync(patchPath, 'utf8')
  } catch (error) {
    log(`读取失败: ${error.message}`)
    process.exit(1)
  }

  if (hasCoralMcp(content)) {
    log('✓ 已检测到 coral 的 MCP 注册（serverName: coral 或 id: mcp-coral）——跳过，不会重复添加。')
    log('  如需改用动态版，请手动删除旧行后重跑本脚本。')
    process.exit(0)
  }

  log('未检测到 coral MCP 注册，准备追加动态版配置……')
  if (dryRun) {
    log('(--dry-run) 以下是即将追加的内容：')
    process.stdout.write(MCP_INSERT)
    process.exit(0)
  }

  // 备份（带时间戳，可回滚）
  const stamp = new Date().toISOString().replace(/[:.]/g, '-')
  const backupPath = `${patchPath}.bak-${stamp}`
  copyFileSync(patchPath, backupPath)
  log(`已备份原文件 → ${backupPath}`)

  // 追加（保证文件以换行结尾再拼）
  const sep = content.endsWith('\n') ? '' : '\n'
  const updated = content + sep + MCP_INSERT
  writeFileSync(patchPath, updated, 'utf8')
  log('✓ 已追加 mcp-coral 动态注册。')
  log('  DSH 对 cordis.patch.yml 有 HMR，保存即生效；开新会话即可用 mcp__coral__* 工具。')
  log('  如遇问题可用备份文件还原：' + backupPath)
}

main()
