#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────────────
// profile_tier.mjs — 用户画像档位 → 上下文/成本参数 软层联动（零依赖 Node ESM，Win/Linux 通用）
//
// 项目：上下文算法优化 + 用户画像联动 · P2 实现 · by 实现P2
// 档位映射表：项目内文档 profile_param_mapping.md（P1 设计）；schema：user_profile.schema.json
//
// 子命令：
//   status              读 $DSH_HOME/coral/user_profile.json（不存在→内置默认 schema 值并提示），
//                       spawn `node webui/lib/cost_probe.mjs balance` 取总余额判档
//                       （spawn 失败/无密钥 → 报「余额未知，按充足档保守展示」），
//                       展示四类参数（compaction/coral 检索/探针告警/输出纪律）当前值 vs 建议值
//   apply [--dry-run]   按档位应用：coral 检索参数仅在实际偏离档位建议时才写 coral_config.json
//                       （原子写 + 先 .bak-<ts> 备份；写后提示「coral 2s 热加载生效」；
//                       当前充足档=默认值 → 打印 no-op 不写）；
//                       探针告警由 cost_probe 读 user_profile.json 生效（无需额外动作）；
//                       compaction preset 只打印切换指引（modelPolicies 三档写法+备份/恢复命令），默认不动文件；
//                       每次 apply 记录一行 method:'tier' 档位快照到 $DSH_HOME/coral/cost_ledger.jsonl
//                       （--dry-run 只打印将要做的动作，不执行任何写入）
//
// 红线：不改 preset / 不改 harness / 不改 coral 运行时逻辑 / 密钥零输出 / 不重启 dsh web。
// 密钥纪律：coral_config.json 的 llm.* 段含 API 密钥——本脚本只读取 retrieval.* 数值，
//           绝不打印/落盘配置全文；写回走文本定点替换，密钥原样保留。
//
// 路径（均可 env 覆盖）：
//   DSH_HOME           默认 ~/.dsh；画像 $DSH_HOME/coral/user_profile.json；账本 $DSH_HOME/coral/cost_ledger.jsonl
//   DSH_CORAL_CONFIG   默认 <cwd>/coral_config.json（coral MCP 的工作区配置文件）
//   DSH_COST_PROBE     默认 <cwd>/webui/lib/cost_probe.mjs
//   DSH_PRESET_YML     默认 $DSH_HOME/.agent-presets/big-project-coordinator/agent.cordis.yml（仅只读扫描）
// ─────────────────────────────────────────────────────────────────────────────
import { readFileSync, writeFileSync, copyFileSync, renameSync, existsSync, mkdirSync, rmSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import os from 'node:os'
import path from 'node:path'

// ── 路径 ─────────────────────────────────────────────────────────────────────
function dshHome() {
  return process.env.DSH_HOME || path.join(os.homedir(), '.dsh')
}
function profilePath() {
  return path.join(dshHome(), 'coral', 'user_profile.json')
}
function ledgerPath() {
  return process.env.DSH_COST_LEDGER || path.join(dshHome(), 'coral', 'cost_ledger.jsonl')
}
function coralConfigPath() {
  return process.env.DSH_CORAL_CONFIG || path.join(process.cwd(), 'coral_config.json')
}
function probePath() {
  return process.env.DSH_COST_PROBE || path.join(process.cwd(), 'webui', 'lib', 'cost_probe.mjs')
}
function presetYmlPath() {
  return process.env.DSH_PRESET_YML || path.join(dshHome(), '.agent-presets', 'big-project-coordinator', 'agent.cordis.yml')
}

// ── 内置默认画像（= schema 默认值；user_profile.json 缺失时兜底，并提示） ─────────
const DEFAULT_PROFILE = {
  profile: {
    tier_thresholds: { normal: 30, generous: 100 },
    parallelism: { main_model_max: 8, external_api_max: 2 },
    hardware: { cpu_cores: 8, ram_gb: 64, gpu: ['5070Ti', '5060Ti'] },
    task_defaults: { top_k: 3, min_score: 0.35 },
  },
  alerts: { hitrate: 0.97, output_ratio: 0.30 },
  compaction: { threshold_ratio: 0.8, retain_ratio: 0.16 },
}

// ── 档位建议值表（P1 映射表 §1；全部为建议值，待 P3 回归验证后定稿） ─────────────
const TIER_TABLE = {
  generous: {
    label: '充足',
    compaction: { thresholdRatio: 0.8, retainRatio: 0.16, maxTokens: 8192 },
    coral: { top_k: 3, min_score: 0.35 },
    alerts: { hitrate: 0.97, output_ratio: 0.30 },
    discipline: '常态（cost-saving ≤300 tokens）',
  },
  normal: {
    label: '正常',
    compaction: { thresholdRatio: 0.85, retainRatio: 0.2, maxTokens: 8192 },
    coral: { top_k: 4, min_score: 0.32 },
    alerts: { hitrate: 0.98, output_ratio: 0.25 },
    discipline: '常态',
  },
  tight: {
    label: '紧张',
    compaction: { thresholdRatio: 0.9, retainRatio: 0.25, maxTokens: 4096 },
    coral: { top_k: 5, min_score: 0.3 },
    alerts: { hitrate: 0.99, output_ratio: 0.2 },
    discipline: 'G/H 全开 + ≤200 tokens',
  },
}

const TIER_ORDER = ['generous', 'normal', 'tight']

// ── 工具 ─────────────────────────────────────────────────────────────────────
function stripBom(raw) {
  return raw.charCodeAt(0) === 0xfeff ? raw.slice(1) : raw
}
function parseRatio(v) {
  const n = typeof v === 'number' ? v : parseFloat(v)
  return Number.isFinite(n) && n > 0 && n <= 1 ? n : NaN
}
function loadJsonFile(p) {
  try {
    return JSON.parse(stripBom(readFileSync(p, 'utf8')))
  } catch (e) {
    if (e.code === 'ENOENT') return null
    throw new Error(`JSON 解析失败 ${p}（${e.message}）`)
  }
}
function tsStamp() {
  const d = new Date()
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`
}
function round2(n) {
  return Math.round(n * 100) / 100
}

// ── 画像装载 ─────────────────────────────────────────────────────────────────
function loadProfile() {
  const p = profilePath()
  const prof = loadJsonFile(p)
  if (!prof) return { profile: structuredClone(DEFAULT_PROFILE), exists: false, path: p }
  // 与默认合并（段/键缺失回落默认，容忍实例不完整；嵌套对象按键深一层合并）
  const merged = structuredClone(DEFAULT_PROFILE)
  for (const section of ['profile', 'alerts', 'compaction']) {
    const src = prof[section]
    if (src && typeof src === 'object') {
      for (const [k, v] of Object.entries(src)) {
        if (v && typeof v === 'object' && merged[section][k] && typeof merged[section][k] === 'object') {
          merged[section][k] = { ...merged[section][k], ...v }
        } else {
          merged[section][k] = v
        }
      }
    }
  }
  return { profile: merged, exists: true, path: p }
}

// ── 余额（spawn cost_probe balance；临时账本路径避免污染真实账本） ───────────────
function fetchBalance() {
  const tmpLedger = path.join(os.tmpdir(), `cost_probe_ledger_${process.pid}.jsonl`)
  const res = spawnSync(process.execPath, [probePath(), 'balance'], {
    encoding: 'utf8',
    timeout: 15_000,
    windowsHide: true,
    env: { ...process.env, DSH_COST_LEDGER: tmpLedger },
  })
  try { rmSync(tmpLedger, { force: true }) } catch { /* best-effort */ }
  if (res.error || res.status !== 0) return null
  const m = /total=([\d.]+)/.exec(res.stdout)
  if (!m) return null
  const n = parseFloat(m[1])
  return Number.isFinite(n) ? n : null
}

// ── 档位判定 ─────────────────────────────────────────────────────────────────
function tierFor(balance, thresholds) {
  if (balance === null) return { tier: 'generous', unknown: true } // 余额未知 → 充足档保守展示
  if (balance < thresholds.normal) return { tier: 'tight', unknown: false }
  if (balance > thresholds.generous) return { tier: 'generous', unknown: false }
  return { tier: 'normal', unknown: false }
}

// ── coral 检索当前值（只读 retrieval.*，绝不动/打印 llm.*） ─────────────────────
function readCoralRetrieval() {
  const p = coralConfigPath()
  if (!existsSync(p)) return { path: p, ok: false, topK: null, minScore: null }
  const data = loadJsonFile(p)
  const r = data?.retrieval
  const topK = typeof r?.top_k === 'number' ? r.top_k : null
  const minScore = typeof r?.min_score === 'number' ? r.min_score : null
  return { path: p, ok: true, topK, minScore }
}

// ── compaction 当前值（preset 只读扫描：有显式 thresholdRatio/retainRatio 用之，否则默认） ─
function readPresetCompaction() {
  const p = presetYmlPath()
  try {
    if (!existsSync(p)) return { path: p, thresholdRatio: null, retainRatio: null, explicit: false }
    const raw = stripBom(readFileSync(p, 'utf8'))
    const tr = /thresholdRatio\s*:\s*([\d.]+)/.exec(raw)
    const rr = /retainRatio\s*:\s*([\d.]+)/.exec(raw)
    if (tr && rr) {
      return { path: p, thresholdRatio: parseFloat(tr[1]), retainRatio: parseFloat(rr[1]), explicit: true }
    }
    return { path: p, thresholdRatio: null, retainRatio: null, explicit: false }
  } catch {
    return { path: p, thresholdRatio: null, retainRatio: null, explicit: false }
  }
}

// ── 探针告警生效阈值（env > user_profile.json alerts > 默认） ──────────────────
function effectiveAlerts(profile) {
  const envHit = parseRatio(process.env.DSH_COST_ALERT_HITRATE)
  const envOut = parseRatio(process.env.DSH_COST_ALERT_OUTPUT_RATIO)
  const a = profile?.alerts || {}
  const profHit = parseRatio(a.hitrate)
  const profOut = parseRatio(a.output_ratio)
  const hit = !Number.isNaN(envHit) ? envHit : !Number.isNaN(profHit) ? profHit : 0.97
  const out = !Number.isNaN(envOut) ? envOut : !Number.isNaN(profOut) ? profOut : 0.3
  const source = !Number.isNaN(envHit) || !Number.isNaN(envOut) ? 'env'
    : !Number.isNaN(profHit) || !Number.isNaN(profOut) ? 'user_profile.json' : 'default'
  return { hit, out, source }
}

// ── coral_config.json 定点写回（文本替换，密钥原样保留；原子写 + .bak-<ts>） ─────
function fmtLike(originalLiteral, value) {
  const m = /\.(\d+)/.exec(originalLiteral)
  return m ? value.toFixed(m[1].length) : String(Math.trunc(value))
}
function replaceOnce(raw, from, to) {
  const parts = raw.split(from)
  if (parts.length !== 2) throw new Error(`定位异常:「${from}」出现 ${parts.length - 1} 次，拒绝写入`)
  return parts.join(to)
}
function patchCoralConfig(p, topK, minScore) {
  const raw = stripBom(readFileSync(p, 'utf8'))
  const tk = /"top_k"\s*:\s*(\d+)/.exec(raw)
  const ms = /"min_score"\s*:\s*([\d.]+)/.exec(raw)
  if (!tk || !ms) throw new Error(`coral_config.json 定位 top_k/min_score 失败（${p}），拒绝写入`)
  const tkLit = tk[1]
  const msLit = ms[1]
  let out = replaceOnce(raw, `"top_k": ${tkLit}`, `"top_k": ${String(topK)}`)
  out = replaceOnce(out, `"min_score": ${msLit}`, `"min_score": ${fmtLike(msLit, minScore)}`)
  const bak = `${p}.bak-${tsStamp()}`
  copyFileSync(p, bak) // 先备份
  const tmp = `${p}.tmp-${process.pid}`
  writeFileSync(tmp, out, 'utf8')
  renameSync(tmp, p) // 原子替换（同盘 rename）
  return { bak, from: { top_k: tkLit, min_score: msLit }, to: { top_k: topK, min_score } }
}

// ── 账本（JSONL，与既有行格式一致可 JSON.parse） ──────────────────────────────
function appendLedger(entry) {
  const p = ledgerPath()
  mkdirSync(path.dirname(p), { recursive: true })
  writeFileSync(p, JSON.stringify(entry) + '\n', { encoding: 'utf8', flag: 'a' })
}

// ── 展示 ─────────────────────────────────────────────────────────────────────
function printTierTable(ctx) {
  const { tier, table, profile } = ctx
  const coralCur = ctx.coralCur
  const presetCur = ctx.presetCur
  const alertsEff = ctx.alertsEff
  const row = (cls, param, cur, sug, note = '') => {
    console.log(`  ${cls.padEnd(11)} ${param.padEnd(14)} ${String(cur).padEnd(16)} ${String(sug).padEnd(16)} ${note}`)
  }
  console.log(`档位映射（当前值 vs 建议值 · ${table.label}档）:`)
  console.log(`  参数类       参数           当前值           建议值         状态`)
  // compaction
  const trCur = presetCur.explicit ? presetCur.thresholdRatio : `${presetCur.thresholdRatio ?? 0.8}(默认)`
  const rrCur = presetCur.explicit ? presetCur.retainRatio : `${presetCur.retainRatio ?? 0.16}(默认)`
  row('compaction', 'thresholdRatio', trCur, table.compaction.thresholdRatio)
  row('compaction', 'retainRatio', rrCur, table.compaction.retainRatio)
  row('compaction', 'maxTokens', '8192(默认)', table.compaction.maxTokens, 'preset 静态，apply 只给指引')
  // coral 检索
  const coralCurStr = coralCur.ok ? `${coralCur.topK}/${coralCur.minScore}` : `未找到 ${path.basename(coralConfigPath())}`
  const coralDev = coralCur.ok && (coralCur.topK !== table.coral.top_k || coralCur.minScore !== table.coral.min_score)
  row('coral 检索', 'top_k/min_score', coralCurStr, `${table.coral.top_k}/${table.coral.min_score}`, coralDev ? '⚠️ apply 将写 coral_config.json' : '✓ 与档位一致')
  // 探针告警
  row('探针告警', 'hitrate', `${alertsEff.hit}(${alertsEff.source})`, table.alerts.hitrate)
  row('探针告警', 'output_ratio', `${alertsEff.out}(${alertsEff.source})`, table.alerts.output_ratio, 'cost_probe 读 user_profile.json 生效')
  // 输出纪律
  row('输出纪律', '严格度', '常态(软层自觉)', table.discipline, 'preset/人格纪律，软层')
  console.log('')
}

// ── status ───────────────────────────────────────────────────────────────────
function cmdStatus() {
  const { profile, exists } = loadProfile()
  const balance = fetchBalance()
  const { tier, unknown } = tierFor(balance, profile.profile.tier_thresholds)
  const table = TIER_TABLE[tier]
  const coralCur = readCoralRetrieval()
  const presetCur = readPresetCompaction()
  const alertsEff = effectiveAlerts(profile)

  console.log(`用户画像档位状态（profile_tier.mjs）`)
  console.log(`画像: ${profilePath()} ${exists ? '（存在）' : '（不存在 → 使用内置默认 schema 值，建议先建实例）'}`)
  console.log(`余额: ${balance === null ? '未知（cost_probe balance 失败/无密钥）' : '¥' + round2(balance) + '（来源: cost_probe balance）'}`
    + (unknown ? ' → **余额未知，按充足档保守展示**' : ` → 档位: ${table.label}`))
  console.log(`阈值: 紧张<${profile.profile.tier_thresholds.normal} / 正常 ${profile.profile.tier_thresholds.normal}~${profile.profile.tier_thresholds.generous} / 充足>${profile.profile.tier_thresholds.generous}`)
  console.log('')
  printTierTable({ tier, table, profile, coralCur, presetCur, alertsEff })
  if (coralCur.ok && coralCur.topK === table.coral.top_k && coralCur.minScore === table.coral.min_score) {
    console.log(`coral 检索: 当前 ${coralCur.topK}/${coralCur.minScore} = 档位建议 → apply 将 no-op（无需写 coral_config.json）`)
  }
}

// ── compaction 切换指引（apply 打印；不动文件） ────────────────────────────────
function printCompactionGuide(tier) {
  const t = TIER_TABLE[tier]
  const preset = presetYmlPath()
  console.log('')
  console.log(`【compaction preset 切换指引】当前档=${TIER_TABLE[tier].label}档（thresholdRatio=${t.compaction.thresholdRatio} / retainRatio=${t.compaction.retainRatio} / maxTokens=${t.compaction.maxTokens}）`)
  console.log('  档位纪律不写进 preset（避免 persona 前缀变体/缓存失效）；compaction 为静态人工档，只在余额档变化时切换：')
  console.log(`  ① 备份当前 preset:`)
  console.log(`     copy  "${preset}" "${preset}.bak-<ts>"`)
  console.log(`  ② 在 agent.cordis.yml 的 compaction 组 compaction-basic 行下加 config（三档只挂一档，modelPolicies target 不重复，重复会加载失败）:`)
  console.log(`       - id: compaction-basic`)
  console.log(`         name: '@deepseek-ai/dsh-compaction-basic'`)
  console.log(`         config:`)
  console.log(`           thresholdRatio: ${t.compaction.thresholdRatio}   # ${TIER_TABLE[tier].label}档`)
  console.log(`           retainRatio: ${t.compaction.retainRatio}     # 须 < thresholdRatio`)
  console.log(`           modelPolicies:`)
  console.log(`             - target: deepseek-official:deepseek-v4-flash`)
  console.log(`               config:`)
  console.log(`                 thresholdRatio: ${t.compaction.thresholdRatio}`)
  console.log(`                 retainRatio: ${t.compaction.retainRatio}`)
  console.log(`  ③ 重挂载 preset / 新会话生效（不重启 dsh web）；恢复=用备份 copy 覆盖回原文件。`)
  console.log('  详见项目内文档 tier_guide.md。')
}

// ── apply ────────────────────────────────────────────────────────────────────
function cmdApply(dryRun) {
  const { profile, exists } = loadProfile()
  const balance = fetchBalance()
  const { tier, unknown } = tierFor(balance, profile.profile.tier_thresholds)
  const table = TIER_TABLE[tier]
  const coralCur = readCoralRetrieval()

  console.log(`profile_tier apply${dryRun ? '（--dry-run，仅打印不执行）' : ''}`)
  console.log(`档位: ${table.label}（余额 ${balance === null ? '未知' : '¥' + round2(balance)}${unknown ? '，按充足档保守展示' : ''}）`)

  // 1) coral 检索：仅实际偏离档位建议才写
  const needWrite = coralCur.ok && (coralCur.topK !== table.coral.top_k || coralCur.minScore !== table.coral.min_score)
  if (!coralCur.ok) {
    console.log(`coral 检索: 未找到 ${coralConfigPath()}（可用 env DSH_CORAL_CONFIG 指定），跳过写回`)
  } else if (!needWrite) {
    console.log(`coral 检索: 当前 ${coralCur.topK}/${coralCur.minScore} = 档位建议 → no-op（无需写 coral_config.json）`)
  } else {
    const target = `${table.coral.top_k}/${table.coral.min_score}`
    console.log(`coral 检索: 当前 ${coralCur.topK}/${coralCur.minScore} 偏离建议 ${target} → ${dryRun ? '将写（dry-run）' : '写回'} coral_config.json`)
    if (!dryRun) {
      const r = patchCoralConfig(coralConfigPath(), table.coral.top_k, table.coral.min_score)
      console.log(`  ✅ 已写 ${coralConfigPath()}（备份 ${path.basename(r.bak)}，${r.from.top_k}/${r.from.min_score} → ${r.to.top_k}/${r.to.min_score}）`)
      console.log(`  coral 2s 热加载生效（无需重启 dsh web）`)
    } else {
      console.log(`  [dry-run] 将原子写 + 先备份 .bak-<ts>；写后提示「coral 2s 热加载生效」`)
    }
  }

  // 2) 探针告警：user_profile.json alerts 由 cost_probe 读取生效，无需额外动作
  const alertsEff = effectiveAlerts(profile)
  console.log(`探针告警: 阈值来源 ${alertsEff.source}（hitrate=${alertsEff.hit} / output_ratio=${alertsEff.out}）→ cost_probe report/by-bucket 自动读取 user_profile.json，无需动作`)

  // 3) compaction preset：只打印指引
  printCompactionGuide(tier)

  // 4) 账本档位快照（每次 apply 一行 method:'tier'；dry-run 不写）
  if (dryRun) {
    console.log('账本: [dry-run] 将记录 1 行 method:"tier" 档位快照 → ' + ledgerPath())
  } else {
    const snapshot = {
      ts: new Date().toISOString(),
      method: 'tier',
      tag: null,
      ok: true,
      tier,
      tierLabel: table.label,
      balance: balance === null ? null : round2(balance),
      balanceSource: balance === null ? 'unknown' : 'cost_probe balance',
      profileExists: exists,
      thresholds: { ...profile.profile.tier_thresholds },
      params: {
        coralCurrent: coralCur.ok ? { top_k: coralCur.topK, min_score: coralCur.minScore } : null,
        coralSuggested: { top_k: table.coral.top_k, min_score: table.coral.min_score },
        coralWrite: needWrite,
        alertsSuggested: { hitrate: table.alerts.hitrate, output_ratio: table.alerts.output_ratio },
        compactionSuggested: { threshold_ratio: table.compaction.thresholdRatio, retain_ratio: table.compaction.retainRatio },
        compaction: 'guide-only',
      },
    }
    appendLedger(snapshot)
    console.log(`账本: 已记录 1 行档位快照 → ${ledgerPath()}`)
  }
}

// ── CLI ──────────────────────────────────────────────────────────────────────
function usage() {
  console.log(`用法: node profile_tier.mjs <status|apply [--dry-run]>

  status              读 $DSH_HOME/coral/user_profile.json（缺失→内置默认并提示），
                      spawn cost_probe balance 判档，展示四类参数当前值 vs 建议值
  apply [--dry-run]   按档位应用：coral 偏离才写 coral_config.json（原子写+.bak-<ts>），
                      compaction 只打印切换指引，探针由 user_profile.json 生效；
                      每次 apply 记录 1 行 method:'tier' 到 $DSH_HOME/coral/cost_ledger.jsonl

环境变量:
  DSH_HOME          默认 ~/.dsh（画像/账本所在）
  DSH_CORAL_CONFIG  覆盖 coral_config.json 路径（默认 <cwd>/coral_config.json）
  DSH_COST_PROBE    覆盖 cost_probe.mjs 路径（默认 <cwd>/webui/lib/cost_probe.mjs）
  DSH_PRESET_YML    覆盖 preset yml 路径（默认 $DSH_HOME/.agent-presets/big-project-coordinator/agent.cordis.yml）

红线: 不改 preset/harness/coral 运行时；密钥零输出；不重启 dsh web。
档位: 余额 < 画像 tier_thresholds.normal → 紧张；normal~generous → 正常；> generous → 充足`)
}

function main() {
  const args = process.argv.slice(2)
  const cmd = args[0] ?? 'status'
  try {
    if (cmd === 'status') cmdStatus()
    else if (cmd === 'apply') cmdApply(args.includes('--dry-run'))
    else if (cmd === '-h' || cmd === '--help') usage()
    else {
      console.error(`[profile_tier] 未知子命令: ${cmd}`)
      usage()
      process.exitCode = 1
    }
  } catch (error) {
    console.error(`[profile_tier] ${error instanceof Error ? error.message : String(error)}`)
    process.exitCode = 1
  }
}

main()
