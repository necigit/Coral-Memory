/**
 * Node-side store and provider-query logic for the balance capsule. Pure
 * Node: file persistence under $DSH_HOME plus HTTP balance queries with the
 * credential seam for key resolution. Nothing here is browser-reachable —
 * the browser half only ever sees the Remote results.
 * @module @deepseek-ai/dsh-client-ui-balance/src/balance-store
 */

import { mkdir, readFile, writeFile } from 'node:fs/promises'
import { homedir } from 'node:os'
import { dirname, join } from 'node:path'
import type { CredentialProvider } from '@deepseek-ai/dsh-credentials'
import { credentialRef } from '@deepseek-ai/dsh-credentials'
import type { BalanceEntry, BalanceInfo, BalanceResult, BalanceStoreFile } from './types.ts'

/** Default DeepSeek API base. */
export const DEEPSEEK_BASE_URL = 'https://api.deepseek.com'
/** Per-query network timeout. */
const QUERY_TIMEOUT_MS = 10_000
/** Credential name seeded into the store on first run. */
const DEFAULT_CREDENTIAL = 'DEEPSEEK_API_KEY'
/** Store file id for the seeded default entry. */
const DEFAULT_ENTRY_ID = 'deepseek-default'

/** Resolve the persistent store file (overridable for tests). */
export function balanceStorePath(file = join(process.env.DSH_HOME ?? join(homedir(), '.dsh'), 'balance.json')): string {
  return file
}

/** Read the store; returns null when absent or malformed. */
export async function readStore(file = balanceStorePath()): Promise<BalanceStoreFile | null> {
  try {
    const raw = await readFile(file, 'utf8')
    const parsed = JSON.parse(raw) as Partial<BalanceStoreFile>
    if (!Array.isArray(parsed.apis)) return null
    return { apis: parsed.apis as BalanceEntry[] }
  } catch {
    return null
  }
}

/** Write the store, creating its directory when needed. */
export async function writeStore(store: BalanceStoreFile, file = balanceStorePath()): Promise<void> {
  await mkdir(dirname(file), { recursive: true })
  await writeFile(file, JSON.stringify(store, null, 2), 'utf8')
}

/**
 * Resolve an entry's key: inline apiKey wins, then the named credential
 * (env first, then the credentials seam).
 * @param entry - stored entry.
 * @param credentials - optional credentials service (absent in bare contexts).
 * @returns the key, or undefined when nothing is configured.
 */
export async function resolveApiKey(
  entry: BalanceEntry,
  credentials: CredentialProvider | undefined,
): Promise<string | undefined> {
  if (typeof entry.apiKey === 'string' && entry.apiKey.length > 0) return entry.apiKey
  if (typeof entry.credential === 'string' && entry.credential.length > 0) {
    const viaEnv = process.env[entry.credential]
    if (typeof viaEnv === 'string' && viaEnv.length > 0) return viaEnv
    if (credentials !== undefined) {
      const resolved = await credentials.resolve(credentialRef(entry.credential))
      if (resolved !== undefined) return resolved.value
    }
  }
  return undefined
}

/**
 * Default seed: one deepseek entry bound to the DEEPSEEK_API_KEY credential,
 * only when that credential actually resolves (so a fresh install with no key
 * yields an empty store instead of a dead row).
 * @param credentials - optional credentials service.
 * @returns the seeded entries (empty when no key exists).
 */
export async function seedDefault(credentials: CredentialProvider | undefined): Promise<BalanceEntry[]> {
  const key = await resolveApiKey({ id: DEFAULT_ENTRY_ID, label: 'DeepSeek', kind: 'deepseek', credential: DEFAULT_CREDENTIAL }, credentials)
  if (key === undefined) return []
  return [{ id: DEFAULT_ENTRY_ID, label: 'DeepSeek', kind: 'deepseek', credential: DEFAULT_CREDENTIAL }]
}

/** Load the store, seeding defaults on first run (persisted only when a seed exists). */
export async function loadStore(credentials: CredentialProvider | undefined, file = balanceStorePath()): Promise<BalanceEntry[]> {
  const existing = await readStore(file)
  if (existing !== null) return existing.apis
  const seeded = await seedDefault(credentials)
  if (seeded.length > 0) await writeStore({ apis: seeded }, file)
  return seeded
}

/** Strip inline keys before anything crosses the Remote boundary. */
export function stripSecrets(entry: BalanceEntry): BalanceEntry {
  if (entry.apiKey === undefined) return entry
  const { apiKey: _stripped, ...rest } = entry
  void _stripped
  return rest
}

/** Parse a provider balance response body into currency buckets. */
export function parseBalanceInfos(body: Record<string, unknown>): BalanceInfo[] | undefined {
  const infos = body['balance_infos']
  if (Array.isArray(infos) && infos.length > 0) {
    const buckets: BalanceInfo[] = []
    for (const raw of infos) {
      if (typeof raw !== 'object' || raw === null) continue
      const record = raw as Record<string, unknown>
      const total = record['total_balance']
      const currency = record['currency']
      if (typeof total !== 'string' && typeof total !== 'number') continue
      buckets.push({ currency: typeof currency === 'string' && currency.length > 0 ? currency : '?', total: String(total) })
    }
    if (buckets.length > 0) return buckets
  }
  const direct = body['balance']
  if (typeof direct === 'number' || (typeof direct === 'string' && direct.length > 0)) {
    return [{ currency: '?', total: String(direct) }]
  }
  const data = typeof body['data'] === 'object' && body['data'] !== null
    ? body['data'] as Record<string, unknown>
    : undefined
  // Zhipu coding-plan quota: data.limits[] with remaining/number per type.
  const limits = data?.['limits']
  if (Array.isArray(limits) && limits.length > 0) {
    const buckets: BalanceInfo[] = []
    for (const raw of limits) {
      if (typeof raw !== 'object' || raw === null) continue
      const record = raw as Record<string, unknown>
      const remaining = record['remaining']
      const total = record['number']
      if (typeof remaining !== 'number' && typeof remaining !== 'string') continue
      if (typeof total !== 'number' && typeof total !== 'string') continue
      const label = typeof record['type'] === 'string' && record['type'].length > 0 ? record['type'] : '配额'
      buckets.push({ currency: label, total: `${String(remaining)}/${String(total)}` })
    }
    if (buckets.length > 0) return buckets
  }
  const nested = data?.['balance']
  if (typeof nested === 'number' || (typeof nested === 'string' && nested.length > 0)) {
    return [{ currency: '?', total: String(nested) }]
  }
  return undefined
}

/**
 * Query one entry and reduce the outcome to a BalanceResult. Never throws:
 * every failure becomes an !ok result so one broken API cannot kill the list.
 * @param entry - stored entry.
 * @param key - resolved API key (undefined → configuration error).
 * @returns the per-entry outcome.
 */
export async function queryBalance(entry: BalanceEntry, key: string | undefined): Promise<BalanceResult> {
  const updatedAt = Date.now()
  const base = { id: entry.id, label: entry.label, updatedAt }
  if (key === undefined) {
    return { ...base, ok: false, error: '未配置 API key（填 apiKey 或 credential）' }
  }
  let url: string
  if (entry.kind === 'deepseek') {
    url = `${entry.baseUrl ?? DEEPSEEK_BASE_URL}/user/balance`
  } else {
    url = entry.balanceUrl ?? ''
    if (url.length === 0) {
      return { ...base, ok: false, error: '自定义接口需要 balanceUrl' }
    }
  }
  try {
    const response = await fetch(url, {
      headers: { Authorization: `Bearer ${key}` },
      signal: AbortSignal.timeout(QUERY_TIMEOUT_MS),
    })
    if (response.status === 401 || response.status === 403) {
      return { ...base, ok: false, error: `认证失败（HTTP ${response.status}）` }
    }
    if (!response.ok) {
      return { ...base, ok: false, error: `HTTP ${response.status}` }
    }
    const body = await response.json() as Record<string, unknown>
    // Surface the provider's own failure message (e.g. Zhipu's coding-plan
    // notice) before falling back to generic parsing.
    const bodyCode = body['code']
    const bodyMsg = body['msg']
    if (body['success'] === false || (typeof bodyCode === 'number' && bodyCode !== 200)) {
      const reason = typeof bodyMsg === 'string' && bodyMsg.length > 0
        ? bodyMsg
        : `provider error（code ${String(bodyCode)}）`
      return { ...base, ok: false, error: reason }
    }
    const infos = parseBalanceInfos(body)
    if (infos === undefined) {
      return { ...base, ok: false, error: '无法解析余额响应' }
    }
    return { ...base, ok: true, available: body['is_available'] !== false, infos }
  } catch (error) {
    return { ...base, ok: false, error: error instanceof Error ? error.message : String(error) }
  }
}
