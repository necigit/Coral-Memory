#!/usr/bin/env node
// ─────────────────────────────────────────────────────────────────────────────
// cost_probe.mjs — Token 价格消耗探针（零依赖，Node ≥ 18，ESM）
//
// 三种口径测「token 花了多少钱」：
//   法一  余额快照差 = 真实花钱（balance / snapshot，账本 delta）
//   法二  投影缓存计价 = 明细 + 命中率（report，验证缓存管线优化效果）
//   法三  DeepSeek 平台用量页对照（人工）
//
// 子命令：
//   report [--day|--all|--top N]   读 projcache，逐会话算 tokens/命中率/成本（峰谷双价）
//   balance                        查 DeepSeek 余额，打印每桶，append 一行到账本
//   snapshot [--tag <备注>]        report 汇总 + balance 快照 append 到账本（JSONL）
//   watch [--watch [秒]]          间隔快照 + 当日阈值状态（默认 300s，Ctrl+C 退出，挂机监控）
//   默认（无子命令）等效 report --all
//
// 监控闭环（阈值告警，默认开启）：
//   report --day      当日命中率 < DSH_COST_ALERT_HITRATE（默认 0.97）→ ⚠️ 前缀亲缘破坏疑似告警
//   report --day / by-bucket  output 谷价成本占比 > DSH_COST_ALERT_OUTPUT_RATIO（默认 0.30）→ ⚠️ 输出偏贵告警
//   告警行以 ⚠️ 开头，打印在人类可读表格之后、SUMMARY_JSON 行之前；
//   SUMMARY_JSON 追加 alerts:[{type,value,threshold,msg}] 字段（无告警时 []，行格式向后兼容）。
//   DSH_COST_ALERT_OFF=1 关闭全部告警；阈值可用 env 覆盖（见 usage / COST_PROBE.md）。
// 档位联动（用户画像，thread 1a31d998d7eca7e4 P2）：env 未设时，$DSH_HOME/coral/user_profile.json
//   的 alerts 字段（hitrate/output_ratio）覆盖默认阈值；无文件/无 alerts 字段回落默认 0.97/0.30。
//   优先级: env > user_profile.json alerts > 默认；SUMMARY_JSON 追加 alertSource 来源字段（向后兼容）。
//
// 数据源：$DSH_HOME/storages/session_projcache.json（env DSH_COST_PROJCACHE 覆盖）
// 账本：  $DSH_HOME/coral/cost_ledger.jsonl   （env DSH_COST_LEDGER 覆盖）
// 价格表：$DSH_HOME/coral/cost_prices.json    存在则覆盖默认（{valley,peak:{hit,miss,output}}）
//
// 桶映射（DeepSeek translate.ts）：命中=cacheReadTokens、未命中=uncachedInputTokens、
// 输出=outputTokens（含 reasoning，不吃缓存折扣）；cacheWriteTokens 恒 0 忽略。
// 命中率 = cacheRead / (cacheRead + uncachedInput)，无请求时 null。
//
// 密钥纪律：只进 Authorization: Bearer 头，绝不打日志 / 不输出 / 不写盘。
// ─────────────────────────────────────────────────────────────────────────────
import { readFile, appendFile, mkdir } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'

const DEEPSEEK_BASE_URL = 'https://api.deepseek.com'
const BALANCE_QUERY_TIMEOUT_MS = 10_000
const UNIT = 1_000_000 // 元/M（价格表单位：每百万 token 的元数）

// 部分 Windows 编辑器会写 UTF-8 BOM（\uFEFF），JSON.parse 与行匹配都对其敏感，统一剥掉
function stripBom(raw) {
  return raw.charCodeAt(0) === 0xfeff ? raw.slice(1) : raw
}

// v4-flash 峰谷价（记忆基线，元/M；可用 cost_prices.json 覆盖）
const DEFAULT_PRICES = {
  valley: { hit: 0.05, miss: 1.5, output: 4.5 },
  peak: { hit: 0.10, miss: 3.0, output: 9.0 },
}

// ── 路径 ─────────────────────────────────────────────────────────────────────
function dshHome() {
  return process.env.DSH_HOME || path.join(os.homedir(), '.dsh')
}
function projcachePath() {
  return process.env.DSH_COST_PROJCACHE || path.join(dshHome(), 'storages', 'session_projcache.json')
}
function ledgerPath() {
  return process.env.DSH_COST_LEDGER || path.join(dshHome(), 'coral', 'cost_ledger.jsonl')
}
function pricesPath() {
  return path.join(dshHome(), 'coral', 'cost_prices.json')
}

// ── 价格表（可配置） ─────────────────────────────────────────────────────────
async function loadPrices() {
  const out = structuredClone(DEFAULT_PRICES)
  try {
    const raw = JSON.parse(stripBom(await readFile(pricesPath(), 'utf8')))
    for (const mode of ['valley', 'peak']) {
      const src = raw[mode]
      if (src && typeof src === 'object') {
        for (const k of ['hit', 'miss', 'output']) {
          if (typeof src[k] === 'number' && Number.isFinite(src[k]) && src[k] >= 0) out[mode][k] = src[k]
        }
      }
    }
    return { prices: out, source: 'cost_prices.json' }
  } catch {
    return { prices: out, source: 'default' }
  }
}

// ── 会话收集（projcache） ────────────────────────────────────────────────────
function toNum(v) {
  if (typeof v === 'number') return Number.isFinite(v) ? v : 0
  if (typeof v === 'string') {
    const n = parseFloat(v)
    return Number.isFinite(n) ? n : 0
  }
  return 0
}

async function loadSessions() {
  const p = projcachePath()
  let raw
  try {
    raw = await readFile(p, 'utf8')
  } catch (e) {
    throw new Error(`无法读取投影缓存 ${p}（${e.message}）。可用 env DSH_COST_PROJCACHE 覆盖路径。`)
  }
  let data
  try {
    data = JSON.parse(stripBom(raw))
  } catch (e) {
    throw new Error(`投影缓存 JSON 解析失败 ${p}（${e.message}）`)
  }
  const tables = data?.tables?.sessions
  if (!tables || typeof tables !== 'object') {
    throw new Error(`投影缓存缺少 tables.sessions 结构：${p}`)
  }
  const sessions = []
  for (const [id, v] of Object.entries(tables)) {
    const totals = v?.rows?.tokenUsage?.val?.totals
    if (!totals || typeof totals !== 'object') continue
    const s = {
      id,
      createdAt: typeof v?.identity?.createdAt === 'number' ? v.identity.createdAt : null,
      cwd: typeof v?.identity?.cwd === 'string' ? v.identity.cwd : '',
      cacheRead: toNum(totals.cacheReadTokens),
      uncached: toNum(totals.uncachedInputTokens),
      output: toNum(totals.outputTokens),
      cacheWrite: toNum(totals.cacheWriteTokens),
    }
    if (s.cacheRead + s.uncached + s.output === 0) continue // 无任何请求的会话不计入
    sessions.push(s)
  }
  sessions.sort((a, b) => (a.createdAt ?? 0) - (b.createdAt ?? 0))
  return sessions
}

// ── 成本 / 命中率 ────────────────────────────────────────────────────────────
function computeOne(s, prices) {
  const denom = s.cacheRead + s.uncached
  const hitRate = denom > 0 ? s.cacheRead / denom : null
  const valley =
    (s.cacheRead * prices.valley.hit + s.uncached * prices.valley.miss + s.output * prices.valley.output) / UNIT
  const peak =
    (s.cacheRead * prices.peak.hit + s.uncached * prices.peak.miss + s.output * prices.peak.output) / UNIT
  return { hitRate, valley, peak, totalTokens: s.cacheRead + s.uncached + s.output }
}

function summarize(sessions, prices) {
  let cacheRead = 0
  let uncached = 0
  let output = 0
  for (const s of sessions) {
    cacheRead += s.cacheRead
    uncached += s.uncached
    output += s.output
  }
  const denom = cacheRead + uncached
  const hitRate = denom > 0 ? cacheRead / denom : null
  const valley = (cacheRead * prices.valley.hit + uncached * prices.valley.miss + output * prices.valley.output) / UNIT
  const peak = (cacheRead * prices.peak.hit + uncached * prices.peak.miss + output * prices.peak.output) / UNIT
  return {
    sessions: sessions.length,
    totalTokens: cacheRead + uncached + output,
    hitRate,
    costValleyYuan: round4(valley),
    costPeakYuan: round4(peak),
  }
}

function round4(n) {
  return Math.round(n * 10_000) / 10_000
}

// ── 监控闭环：阈值告警（默认开启，env 可关/调阈值；用户画像 alerts 覆盖默认） ────────
// 阈值优先级（逐键级联）: env DSH_COST_ALERT_* > user_profile.json alerts > 内置默认(0.97/0.30)
async function profileAlertThresholds() {
  try {
    const raw = JSON.parse(stripBom(await readFile(path.join(dshHome(), 'coral', 'user_profile.json'), 'utf8')))
    const a = raw?.alerts
    const ratio = (v) => (typeof v === 'number' && Number.isFinite(v) && v > 0 && v <= 1 ? v : NaN)
    const hitRate = ratio(a?.hitrate)
    const outputRatio = ratio(a?.output_ratio)
    if (!Number.isNaN(hitRate) || !Number.isNaN(outputRatio)) {
      return { hitRate, outputRatio }
    }
  } catch { /* 无文件/解析失败 → 回落默认 */ }
  return null
}

async function loadAlertConfig() {
  const ratio = (s) => {
    const n = parseFloat(s)
    return Number.isFinite(n) && n > 0 && n <= 1 ? n : NaN
  }
  const envHit = ratio(process.env.DSH_COST_ALERT_HITRATE ?? '')
  const envOut = ratio(process.env.DSH_COST_ALERT_OUTPUT_RATIO ?? '')
  const envHitOk = !Number.isNaN(envHit)
  const envOutOk = !Number.isNaN(envOut)
  let hitRate = 0.97
  let outputRatio = 0.30
  let source = 'default'
  if (!envHitOk || !envOutOk) {
    const prof = await profileAlertThresholds()
    if (prof) {
      if (!envHitOk && !Number.isNaN(prof.hitRate)) hitRate = prof.hitRate
      if (!envOutOk && !Number.isNaN(prof.outputRatio)) outputRatio = prof.outputRatio
      source = 'user_profile.json'
    }
  }
  if (envHitOk) hitRate = envHit
  if (envOutOk) outputRatio = envOut
  if (envHitOk || envOutOk) source = 'env'
  return {
    off: process.env.DSH_COST_ALERT_OFF === '1',
    hitRate,
    outputRatio,
    source, // 阈值来源: env | user_profile.json | default（SUMMARY_JSON alertSource）
  }
}

// 一组会话的聚合指标：命中率 + output 谷价成本占比（无会话/无成本时为 null）
function aggMetrics(list, prices) {
  let cr = 0
  let unc = 0
  let out = 0
  for (const s of list) {
    cr += s.cacheRead
    unc += s.uncached
    out += s.output
  }
  const denom = cr + unc
  const v = prices.valley
  const totCost = (cr * v.hit + unc * v.miss + out * v.output) / UNIT
  const outCost = (out * v.output) / UNIT
  return {
    sessions: list.length,
    hitRate: denom > 0 ? cr / denom : null,
    outputCostRatio: totCost > 0 ? outCost / totCost : null,
  }
}

// 「当日」= 本地日期与今天一致的会话集合
function todayAggMetrics(sessions, prices) {
  const today = localDate(Date.now())
  return aggMetrics(sessions.filter((s) => localDate(s.createdAt) === today), prices)
}

// 由当日指标生成告警；无告警返回 []（value/threshold 为 0~1 归一值，msg 带百分比）
function buildAlerts(cfg, m) {
  if (cfg.off) return []
  const alerts = []
  if (m.hitRate !== null && m.hitRate < cfg.hitRate) {
    alerts.push({
      type: 'hitrate',
      value: round4(m.hitRate),
      threshold: cfg.hitRate,
      msg: `当日命中率 ${(m.hitRate * 100).toFixed(2)}% < 阈值 ${(cfg.hitRate * 100).toFixed(2)}%，疑似前缀亲缘破坏（新会话/记忆注入/压缩回放变动），建议排查`,
    })
  }
  if (m.outputCostRatio !== null && m.outputCostRatio > cfg.outputRatio) {
    alerts.push({
      type: 'output-ratio',
      value: round4(m.outputCostRatio),
      threshold: cfg.outputRatio,
      msg: `output 占成本 ${(m.outputCostRatio * 100).toFixed(2)}%，输出偏贵，建议压缩输出/渐进披露`,
    })
  }
  return alerts
}

function printAlerts(alerts) {
  for (const a of alerts) console.log(`⚠️ [${a.type}] ${a.msg}`)
}

// ── 展示 ─────────────────────────────────────────────────────────────────────
function localDate(ts) {
  if (ts === null || ts === undefined) return '-'
  const d = new Date(ts)
  const p = (n) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

function fmtPct(hr) {
  return hr === null ? '   -' : `${(hr * 100).toFixed(2).padStart(5)}%`
}

function fmtTok(n) {
  return n.toLocaleString('en-US')
}

function trunc(s, max) {
  return s.length > max ? s.slice(0, max - 1) + '…' : s
}

function shortId(id) {
  // session-<uuid> → session-<前8位>；裸 uuid → 前8位
  const m = /^session-(.{8})/.exec(id)
  return m ? `session-${m[1]}` : id.slice(0, 12)
}

function printTableHeader() {
  console.log(
    `${'ID'.padEnd(18)} | ${'日期(本地)'.padEnd(11)} | ${'命中率'.padStart(7)} | ${'cacheRead'.padStart(13)} | ${'uncached'.padStart(11)} | ${'output'.padStart(10)} | ${'谷价¥'.padStart(8)} | ${'峰价¥'.padStart(8)} | cwd`
  )
  console.log('-'.repeat(120))
}

function printSessionRow(s, c, prices) {
  console.log(
    `${shortId(s.id).padEnd(18)} | ${localDate(s.createdAt).padEnd(11)} | ${fmtPct(c.hitRate)} | ${fmtTok(s.cacheRead).padStart(13)} | ${fmtTok(s.uncached).padStart(11)} | ${fmtTok(s.output).padStart(10)} | ${c.valley.toFixed(2).padStart(8)} | ${c.peak.toFixed(2).padStart(8)} | ${trunc(s.cwd, 44)}`
  )
}

function printSummary(sum, prices, pricesSource, mode, byDay, alerts, alertSource) {
  const line = {
    ts: new Date().toISOString(),
    mode,
    prices: { valley: prices.valley, peak: prices.peak },
    pricesSource,
    ...sum,
  }
  if (byDay) line.byDay = byDay
  // 监控闭环：追加 alerts 字段（无告警时 []），其余字段/行格式不变，保持向后兼容
  line.alerts = alerts ?? []
  // 档位联动（P2）：阈值来源（env | user_profile.json | default），向后兼容可选字段
  if (alertSource) line.alertSource = alertSource
  console.log('')
  console.log(`SUMMARY_JSON ${JSON.stringify(line)}`)
}

// ── report 子命令 ────────────────────────────────────────────────────────────
async function cmdReport(opts) {
  const { prices, source: pricesSource } = await loadPrices()
  const sessions = await loadSessions()
  if (sessions.length === 0) {
    console.log(`投影缓存无有效会话（${projcachePath()}）`)
  }

  console.log(`Token 价格消耗探针 — 数据源: ${projcachePath()}`)
  console.log(
    `价格表（元/M）: 谷 命中${prices.valley.hit}/未命中${prices.valley.miss}/输出${prices.valley.output}，峰 ${prices.peak.hit}/${prices.peak.miss}/${prices.peak.output}（来源: ${pricesSource}）`
  )

  // 监控闭环：仅 report --day 跑当日阈值告警（--all/--top 不告警，alerts 恒 []）
  const alertCfg = await loadAlertConfig()
  let alerts = []
  if (opts.day) alerts = buildAlerts(alertCfg, todayAggMetrics(sessions, prices))

  let byDay
  if (opts.day) {
    // 按天聚合
    const groups = new Map()
    for (const s of sessions) {
      const day = localDate(s.createdAt)
      if (!groups.has(day)) groups.set(day, [])
      groups.get(day).push(s)
    }
    const days = [...groups.keys()].sort()
    printTableHeader()
    byDay = []
    for (const day of days) {
      const g = groups.get(day)
      const agg = {
        id: `[${g.length} 会话]`,
        createdAt: null,
        cwd: day,
        cacheRead: g.reduce((a, s) => a + s.cacheRead, 0),
        uncached: g.reduce((a, s) => a + s.uncached, 0),
        output: g.reduce((a, s) => a + s.output, 0),
      }
      const c = computeOne(agg, prices)
      printSessionRow(agg, c, prices)
      byDay.push({ day, sessions: g.length, hitRate: c.hitRate, costValleyYuan: round4(c.valley), costPeakYuan: round4(c.peak) })
    }
  } else {
    // 逐会话（默认 / --all / --top N）
    let list = sessions
    if (opts.top !== null) {
      list = [...sessions].sort((a, b) => computeOne(b, prices).valley - computeOne(a, prices).valley).slice(0, opts.top)
    }
    printTableHeader()
    for (const s of list) {
      printSessionRow(s, computeOne(s, prices), prices)
    }
  }

  printAlerts(alerts)
  const sum = summarize(sessions, prices)
  printSummary(sum, prices, pricesSource, opts.day ? 'day' : opts.top !== null ? `top${opts.top}` : 'all', byDay, alerts, alertCfg.source)
}

// ── 余额查询（照 index.js queryBalance 语义：inline apiKey > env(credential) > .credentials.yaml） ──
function balanceStorePath() {
  return path.join(dshHome(), 'balance.json')
}

async function readBalanceStore() {
  try {
    const parsed = JSON.parse(stripBom(await readFile(balanceStorePath(), 'utf8')))
    return Array.isArray(parsed.apis) ? parsed.apis : []
  } catch {
    return []
  }
}

function resolveApiKey(entry) {
  if (typeof entry.apiKey === 'string' && entry.apiKey.length > 0) return entry.apiKey
  if (typeof entry.credential === 'string' && entry.credential.length > 0) {
    const viaEnv = process.env[entry.credential]
    if (typeof viaEnv === 'string' && viaEnv.length > 0) return viaEnv
  }
  return undefined
}

async function resolveCredentialFromStore(name) {
  try {
    const raw = stripBom(await readFile(path.join(dshHome(), '.credentials.yaml'), 'utf8'))
    for (const line of raw.split(/\r?\n/)) {
      const trimmed = line.trim()
      if (!trimmed || trimmed.startsWith('#')) continue
      const idx = trimmed.indexOf(':')
      if (idx <= 0) continue
      if (trimmed.slice(0, idx).trim() === name) {
        const value = trimmed.slice(idx + 1).trim().replace(/^["']|["']$/g, '')
        if (value.length > 0) return value
      }
    }
  } catch {
    /* 文件缺失/不可读 → 该凭据解析失败 */
  }
  return undefined
}

function parseBalanceInfos(body) {
  const infos = body.balance_infos
  if (!Array.isArray(infos) || infos.length === 0) return undefined
  const buckets = []
  for (const raw of infos) {
    if (typeof raw !== 'object' || raw === null) continue
    const total = raw.total_balance
    const currency = raw.currency
    if (typeof total !== 'string' && typeof total !== 'number') continue
    buckets.push({
      currency: typeof currency === 'string' && currency.length > 0 ? currency : '?',
      total: String(total),
      granted: typeof raw.granted_balance === 'number' || typeof raw.granted_balance === 'string' ? String(raw.granted_balance) : null,
      toppedUp: typeof raw.topped_up_balance === 'number' || typeof raw.topped_up_balance === 'string' ? String(raw.topped_up_balance) : null,
    })
  }
  return buckets.length > 0 ? buckets : undefined
}

async function queryOne(entry) {
  const base = { label: entry.label ?? entry.id ?? '未知' }
  let key = resolveApiKey(entry)
  if (key === undefined && typeof entry.credential === 'string') {
    key = await resolveCredentialFromStore(entry.credential)
  }
  if (key === undefined) {
    return [{ ...base, ok: false, error: '未配置 API key（填 apiKey 或 credential，或在 .credentials.yaml 配置）' }]
  }
  if (entry.kind !== 'deepseek') {
    return [{ ...base, ok: false, error: '仅支持 kind=deepseek 的余额查询' }]
  }
  try {
    const response = await fetch(`${entry.baseUrl ?? DEEPSEEK_BASE_URL}/user/balance`, {
      headers: { Authorization: `Bearer ${key}` },
      signal: AbortSignal.timeout(BALANCE_QUERY_TIMEOUT_MS),
    })
    if (response.status === 401 || response.status === 403) {
      return [{ ...base, ok: false, error: `认证失败（HTTP ${response.status}）` }]
    }
    if (!response.ok) {
      return [{ ...base, ok: false, error: `HTTP ${response.status}` }]
    }
    const body = await response.json()
    const bodyCode = body.code
    const bodyMsg = body.msg
    if (body.success === false || (typeof bodyCode === 'number' && bodyCode !== 200)) {
      return [{
        ...base,
        ok: false,
        error: typeof bodyMsg === 'string' && bodyMsg.length > 0 ? bodyMsg : `provider error（code ${String(bodyCode)}）`,
      }]
    }
    const infos = parseBalanceInfos(body)
    if (infos === undefined) {
      return [{ ...base, ok: false, error: '无法解析余额响应' }]
    }
    return infos.map((b) => ({ ...base, ok: true, currency: b.currency, total: b.total, granted: b.granted, toppedUp: b.toppedUp }))
  } catch (error) {
    return [{ ...base, ok: false, error: error instanceof Error ? error.message : String(error) }]
  }
}

async function queryAllBalances() {
  const entries = await readBalanceStore()
  if (entries.length === 0) {
    return [{ label: '?', ok: false, error: `balance.json 无 apis 条目（${balanceStorePath()}）` }]
  }
  const rows = []
  for (const entry of entries) {
    rows.push(...(await queryOne(entry)))
  }
  return rows
}

// ── 账本 ─────────────────────────────────────────────────────────────────────
async function appendLedger(entry) {
  const p = ledgerPath()
  await mkdir(path.dirname(p), { recursive: true })
  await appendFile(p, JSON.stringify(entry) + '\n', 'utf8')
}

// ── balance 子命令 ───────────────────────────────────────────────────────────
async function cmdBalance(opts) {
  const rows = await queryAllBalances()
  const ts = new Date().toISOString()
  const okRows = rows.filter((r) => r.ok)
  const failed = rows.filter((r) => !r.ok)

  console.log(`DeepSeek 余额（${ts}）`)
  if (okRows.length > 0) {
    for (const r of okRows) {
      const granted = r.granted === null ? '-' : r.granted
      const toppedUp = r.toppedUp === null ? '-' : r.toppedUp
      console.log(`  [${r.label}] ${r.currency}: total=${r.total}  granted=${granted}  topped_up=${toppedUp}`)
    }
  }
  if (failed.length > 0) {
    for (const r of failed) {
      console.error(`  [${r.label}] 失败: ${r.error}`)
    }
  }

  // append 一行到账本
  await appendLedger({
    ts,
    method: 'balance',
    tag: opts.tag ?? null,
    ok: okRows.length > 0,
    buckets: okRows.map((r) => ({ label: r.label, currency: r.currency, total: r.total, granted: r.granted, toppedUp: r.toppedUp })),
    errors: failed.map((r) => r.error),
  })
  console.log(`已记录账本: ${ledgerPath()}`)

  if (okRows.length === 0) {
    process.exitCode = 1
    console.error('[cost_probe] 余额查询全部失败（未配置密钥 / 无网络 / provider 错误）。用法见 COST_PROBE.md。')
    return
  }
}

// ── snapshot 子命令 ──────────────────────────────────────────────────────────
async function cmdSnapshot(opts) {
  const ts = new Date().toISOString()
  const { prices, source: pricesSource } = await loadPrices()
  const sessions = await loadSessions()
  const sum = summarize(sessions, prices)

  const lines = [
    {
      ts,
      method: 'projcache',
      tag: opts.tag ?? null,
      prices: { valley: prices.valley, peak: prices.peak },
      pricesSource,
      summary: sum,
    },
  ]
  const rows = await queryAllBalances()
  const okRows = rows.filter((r) => r.ok)
  lines.push({
    ts,
    method: 'balance',
    tag: opts.tag ?? null,
    ok: okRows.length > 0,
    buckets: okRows.map((r) => ({ label: r.label, currency: r.currency, total: r.total, granted: r.granted, toppedUp: r.toppedUp })),
    errors: rows.filter((r) => !r.ok).map((r) => r.error),
  })

  for (const line of lines) {
    await appendLedger(line)
  }
  console.log(`快照已写入账本（${lines.length} 行）: ${ledgerPath()}`)
  console.log(`  projcache 汇总: sessions=${sum.sessions} totalTokens=${fmtTok(sum.totalTokens)} hitRate=${sum.hitRate === null ? 'null' : (sum.hitRate * 100).toFixed(2) + '%'} 谷价=¥${sum.costValleyYuan.toFixed(2)} 峰价=¥${sum.costPeakYuan.toFixed(2)}`)
  for (const r of okRows) {
    console.log(`  balance: [${r.label}] ${r.currency} total=${r.total}`)
  }
  const failed = rows.filter((r) => !r.ok)
  if (failed.length > 0) {
    for (const r of failed) {
      console.error(`  balance 失败: [${r.label}] ${r.error}`)
    }
  }
}

// ── by-bucket 子命令：三桶成本分解 + 压缩建议 ─────────────────────────────────
async function cmdByBucket() {
  const { prices } = await loadPrices()
  const sessions = await loadSessions()
  if (sessions.length === 0) {
    console.error('[cost_probe] 无计费会话（投影缓存为空）')
    process.exitCode = 1
    return
  }
  let cr = 0, unc = 0, out = 0
  for (const s of sessions) { cr += s.cacheRead; unc += s.uncached; out += s.output }
  const tot = cr + unc + out
  const denom = cr + unc
  const hitRate = denom > 0 ? cr / denom : null
  const v = prices.valley, pk = prices.peak
  const cv = { hit: cr * v.hit / UNIT, miss: unc * v.miss / UNIT, output: out * v.output / UNIT }
  const ck = { hit: cr * pk.hit / UNIT, miss: unc * pk.miss / UNIT, output: out * pk.output / UNIT }
  const cvTot = cv.hit + cv.miss + cv.output
  const ckTot = ck.hit + ck.miss + ck.output
  console.log(`Token 桶成本分解（${sessions.length} 会话）— 价格表: 谷 命中${v.hit}/未命中${v.miss}/输出${v.output}，峰 ${pk.hit}/${pk.miss}/${pk.output}（元/M）`)
  console.log('-'.repeat(90))
  console.log(`  cacheRead  ${fmtTok(cr).padStart(14)}  (${(cr / tot * 100).toFixed(2)}%)  谷¥${cv.hit.toFixed(2).padStart(8)}  峰¥${ck.hit.toFixed(2).padStart(8)}`)
  console.log(`  uncached   ${fmtTok(unc).padStart(14)}  (${(unc / tot * 100).toFixed(2)}%)  谷¥${cv.miss.toFixed(2).padStart(8)}  峰¥${ck.miss.toFixed(2).padStart(8)}`)
  console.log(`  output     ${fmtTok(out).padStart(14)}  (${(out / tot * 100).toFixed(2)}%)  谷¥${cv.output.toFixed(2).padStart(8)}  峰¥${ck.output.toFixed(2).padStart(8)}`)
  console.log(`  TOTAL      ${fmtTok(tot).padStart(14)}  (100.00%)  谷¥${cvTot.toFixed(2).padStart(8)}  峰¥${ckTot.toFixed(2).padStart(8)}`)
  console.log('-'.repeat(90))
  console.log(`  命中率=${hitRate === null ? 'null' : (hitRate * 100).toFixed(2) + '%'}  output占总token=${(out / tot * 100).toFixed(2)}%  uncached占输入=${(unc / denom * 100).toFixed(2)}%`)
  console.log(`  单位成本比(谷): 命中1 : 未命中${(v.miss / v.hit).toFixed(0)} : 输出${(v.output / v.hit).toFixed(0)}  → 输出桶单价最高，优先压缩`)
  console.log(`  建议: output -30% ≈ 省¥${(cv.output * 0.3).toFixed(2)}(谷)/¥${(ck.output * 0.3).toFixed(2)}(峰)；uncached -40% ≈ 省¥${(cv.miss * 0.4).toFixed(2)}(谷)/¥${(ck.miss * 0.4).toFixed(2)}(峰)`)

  // 监控闭环：by-bucket 按「当日」口径查 output 成本占比（与 report --day 同一语义；
  // 注意与命令自身全量展示口径不同，见 COST_PROBE.md；命中率告警只属 report --day）
  const alertCfg = await loadAlertConfig()
  const alerts = buildAlerts(alertCfg, { hitRate: null, outputCostRatio: todayAggMetrics(sessions, prices).outputCostRatio })
  printAlerts(alerts)

  console.log('SUMMARY_JSON ' + JSON.stringify({
    ts: new Date().toISOString(),
    mode: 'by-bucket',
    prices: { valley: v, peak: pk },
    pricesSource: 'default',
    alertSource: alertCfg.source,
    sessions: sessions.length,
    buckets: { cacheRead: cr, uncached: unc, output: out, total: tot },
    hitRate,
    costValleyYuan: round4(cvTot),
    costPeakYuan: round4(ckTot),
    costByBucket: { valley: { hit: round4(cv.hit), miss: round4(cv.miss), output: round4(cv.output) }, peak: { hit: round4(ck.hit), miss: round4(ck.miss), output: round4(ck.output) } },
    unitRatioValley: { hit: 1, miss: round4(v.miss / v.hit), output: round4(v.output / v.hit) },
    compressSuggest: { outputMinus30: { valley: round4(cv.output * 0.3), peak: round4(ck.output * 0.3) }, uncachedMinus40: { valley: round4(cv.miss * 0.4), peak: round4(ck.miss * 0.4) } },
    alerts,
  }))
}

// ── watch 子命令：间隔快照 + 当日阈值状态（挂机监控，不写账本、不查余额） ─────────
async function cmdWatch(intervalSec) {
  const interval = intervalSec && intervalSec > 0 ? intervalSec : 300
  const cfg = await loadAlertConfig()
  console.log(`[cost_probe watch] 每 ${interval}s 检查当日阈值状态（Ctrl+C 退出；告警阈值: 命中率<${(cfg.hitRate * 100).toFixed(2)}% / output成本占比>${(cfg.outputRatio * 100).toFixed(2)}%）`)
  const tick = async () => {
    try {
      const { prices } = await loadPrices()
      const sessions = await loadSessions()
      const m = todayAggMetrics(sessions, prices)
      const alerts = buildAlerts(cfg, m)
      const ts = new Date().toISOString()
      console.log(
        `[${ts}] 会话=${m.sessions} 今日命中率=${m.hitRate === null ? 'null' : (m.hitRate * 100).toFixed(2) + '%'} output成本占比=${m.outputCostRatio === null ? 'null' : (m.outputCostRatio * 100).toFixed(2) + '%'} 告警=${alerts.length}`
      )
      printAlerts(alerts)
    } catch (error) {
      console.error(`[cost_probe watch] ${error instanceof Error ? error.message : String(error)}`)
    }
  }
  await tick()
  const timer = setInterval(tick, interval * 1000)
  const stop = () => {
    console.log('\n[cost_probe watch] 已退出')
    clearInterval(timer)
    process.exit(0)
  }
  process.on('SIGINT', stop)
  process.on('SIGTERM', stop)
}

// ── CLI ──────────────────────────────────────────────────────────────────────
function usage() {
  console.log(`用法: node webui/lib/cost_probe.mjs <子命令> [选项]

子命令:
  report [--day|--all|--top N]   读投影缓存，逐会话算 tokens/命中率/成本（峰谷双价）
                                 --day  按 createdAt 按天聚合
                                 --all  全量逐会话（默认）
                                 --top N 只列谷价成本最高的 N 个会话（汇总仍为全量）
  balance                        查 DeepSeek 余额，打印每桶（currency/total/granted/topped_up），append 一行到账本
  snapshot [--tag <备注>]        report 汇总 + balance 快照 append 到账本（JSONL）
  by-bucket                     三桶成本分解（命中/未命中/输出）+ 占比 + 压缩建议（SUMMARY_JSON 尾行）
  watch [--watch [秒]]          间隔快照 + 当日阈值状态（默认 300s，Ctrl+C 退出，挂机监控）
  默认（无子命令）等效 report --all

监控闭环（阈值告警，默认开启；report --day 检查命中率，report --day/by-bucket 检查 output 成本占比）:
  ⚠️ 告警行打印在表格之后、SUMMARY_JSON 之前；SUMMARY_JSON 追加 alerts:[{type,value,threshold,msg}]
  DSH_COST_ALERT_HITRATE       当日命中率阈值，默认 0.97（跌破告警：疑似前缀亲缘破坏）
  DSH_COST_ALERT_OUTPUT_RATIO  output 谷价成本占比阈值，默认 0.30（超标告警：输出偏贵）
  DSH_COST_ALERT_OFF           设为 1 关闭全部阈值告警
  阈值优先级: env > $DSH_HOME/coral/user_profile.json 的 alerts 字段 > 默认（SUMMARY_JSON 附 alertSource）

环境变量:
  DSH_HOME            默认 ~/.dsh
  DSH_COST_PROJCACHE  覆盖投影缓存路径
  DSH_COST_LEDGER     覆盖账本路径

价格表: $DSH_HOME/coral/cost_prices.json（可选）→ {"valley":{"hit","miss","output"},"peak":{...}}
密钥:   inline apiKey > 环境变量 credential > $DSH_HOME/.credentials.yaml 兜底，只进 Authorization 头`)
}

function parseArgs(argv) {
  let cmd = argv[0] ?? 'report'
  let rest = argv.slice(1)
  const opts = { day: false, top: null, tag: null, help: false, watch: null }
  // 子命令位置也可以是 -h/--help
  if (cmd === '-h' || cmd === '--help') {
    cmd = 'report'
    opts.help = true
  } else if (cmd.startsWith('-')) {
    // 首参即选项（如 --watch 60 / --watch=60）→ 并入选项流，子命令回落 report
    rest = [cmd, ...rest]
    cmd = 'report'
  }
  for (let i = 0; i < rest.length; i++) {
    const a = rest[i]
    if (a === '--day') opts.day = true
    else if (a === '--all') { /* 默认 */ }
    else if (a === '--top') {
      const v = parseInt(rest[++i], 10)
      opts.top = Number.isFinite(v) && v > 0 ? v : null
    } else if (a.startsWith('--top=')) {
      const v = parseInt(a.slice(6), 10)
      opts.top = Number.isFinite(v) && v > 0 ? v : null
    } else if (a === '--watch') {
      // 可选秒数：下一个 token 为纯数字则消费，否则默认 300
      const next = rest[i + 1]
      if (next !== undefined && /^\d+$/.test(next)) {
        opts.watch = parseInt(next, 10)
        i++
      } else {
        opts.watch = 300
      }
      cmd = 'watch'
    } else if (a.startsWith('--watch=')) {
      const v = parseInt(a.slice(8), 10)
      opts.watch = Number.isFinite(v) && v > 0 ? v : 300
      cmd = 'watch'
    } else if (a === '--tag') {
      opts.tag = rest[++i] ?? null
    } else if (a.startsWith('--tag=')) {
      opts.tag = a.slice(6)
    } else if (a === '-h' || a === '--help') {
      opts.help = true
    } else {
      console.error(`[cost_probe] 未知参数: ${a}`)
      opts.help = true
    }
  }
  return { cmd, opts }
}

async function main() {
  const { cmd, opts } = parseArgs(process.argv.slice(2))
  if (opts.help) {
    usage()
    return
  }
  if (!['report', 'balance', 'snapshot', 'by-bucket', 'watch'].includes(cmd)) {
    console.error(`[cost_probe] 未知子命令: ${cmd}`)
    usage()
    process.exitCode = 1
    return
  }
  try {
    if (cmd === 'report') await cmdReport(opts)
    else if (cmd === 'balance') await cmdBalance(opts)
    else if (cmd === 'by-bucket') await cmdByBucket()
    else if (cmd === 'watch') await cmdWatch(opts.watch)
    else await cmdSnapshot(opts)
  } catch (error) {
    console.error(`[cost_probe] ${error instanceof Error ? error.message : String(error)}`)
    process.exitCode = 1
  }
}

main()
