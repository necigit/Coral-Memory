/**
 * API balance capsule, node half. Hosts the `balance` Typert Remote service:
 * a persistent multi-API entry store ($DSH_HOME/balance.json) plus per-entry
 * balance queries over plain fetch. Keys never cross the Remote boundary —
 * list() strips inline keys, and resolution happens per query through the
 * credentials seam / environment. The browser half only ever receives
 * BalanceEntry (sans secrets) and BalanceResult payloads, so the capsule
 * costs zero model tokens: no agent, no context, no session involvement.
 * @module @deepseek-ai/dsh-client-ui-balance
 */

import { randomUUID } from 'node:crypto'
import type { Context } from '@deepseek-ai/cordis'
import type { CredentialProvider } from '@deepseek-ai/dsh-credentials'
import { Remote, TypertRemoteService } from '@deepseek-ai/dsh-typert-protocol'
import {
  loadStore,
  queryBalance,
  resolveApiKey,
  stripSecrets,
  writeStore,
} from './balance-store.ts'
import type { BalanceEntry, BalanceEntryInput, BalanceResult } from './types.ts'

export type {
  BalanceEntry, BalanceEntryInput, BalanceInfo, BalanceKind, BalanceResult, BalanceStoreFile,
} from './types.ts'

/** The `balance` Remote service: store CRUD + query, all session-free. */
export class BalanceService extends TypertRemoteService {
  constructor(ctx: Context) {
    super(ctx, 'balance')
  }

  /** Optional credential seam; absent in bare contexts (env fallback still applies). */
  private credentials(): CredentialProvider | undefined {
    return this.ctx.get('credentials')
  }

  /** Current entries, seeding the default DeepSeek row on first run. */
  private async load(): Promise<BalanceEntry[]> {
    return await loadStore(this.credentials())
  }

  /** List configured entries; inline keys are never echoed. */
  @Remote('list')
  async list(): Promise<BalanceEntry[]> {
    return (await this.load()).map(stripSecrets)
  }

  /** Query every configured entry and return per-entry outcomes. */
  @Remote('query')
  async query(): Promise<BalanceResult[]> {
    const entries = await this.load()
    const credentials = this.credentials()
    return await Promise.all(entries.map(async (entry) => {
      const key = await resolveApiKey(entry, credentials)
      return await queryBalance(entry, key)
    }))
  }

  /** Add one API entry and return the fresh (secrets-stripped) list. */
  @Remote('add')
  async add(entry: BalanceEntryInput): Promise<BalanceEntry[]> {
    const label = entry.label.trim()
    if (label.length === 0) throw new Error('balance: label 不能为空')
    if (entry.kind !== 'deepseek' && entry.kind !== 'custom') {
      throw new Error('balance: kind 必须是 deepseek 或 custom')
    }
    const apis = await this.load()
    const created: BalanceEntry = { ...entry, label, id: `api-${randomUUID().slice(0, 8)}` }
    apis.push(created)
    await writeStore({ apis })
    return apis.map(stripSecrets)
  }

  /** Remove one API entry and return the fresh list. */
  @Remote('remove')
  async remove(id: string): Promise<BalanceEntry[]> {
    const apis = await this.load()
    const next = apis.filter(entry => entry.id !== id)
    if (next.length === apis.length) throw new Error(`balance: 未知条目 "${id}"`)
    await writeStore({ apis: next })
    return next.map(stripSecrets)
  }

  /** Patch one API entry and return the fresh list. */
  @Remote('update')
  async update(id: string, patch: Partial<BalanceEntryInput>): Promise<BalanceEntry[]> {
    const apis = await this.load()
    const at = apis.findIndex(entry => entry.id === id)
    if (at < 0) throw new Error(`balance: 未知条目 "${id}"`)
    const current = apis[at]
    if (current === undefined) throw new Error(`balance: 未知条目 "${id}"`)
    const merged: BalanceEntry = {
      id: current.id,
      label: patch.label ?? current.label,
      kind: patch.kind ?? current.kind,
    }
    if (patch.baseUrl !== undefined) merged.baseUrl = patch.baseUrl
    else if (current.baseUrl !== undefined) merged.baseUrl = current.baseUrl
    if (patch.balanceUrl !== undefined) merged.balanceUrl = patch.balanceUrl
    else if (current.balanceUrl !== undefined) merged.balanceUrl = current.balanceUrl
    if (patch.apiKey !== undefined) merged.apiKey = patch.apiKey
    else if (current.apiKey !== undefined) merged.apiKey = current.apiKey
    if (patch.credential !== undefined) merged.credential = patch.credential
    else if (current.credential !== undefined) merged.credential = current.credential
    if (merged.label.trim().length === 0) throw new Error('balance: label 不能为空')
    apis[at] = merged
    await writeStore({ apis })
    return apis.map(stripSecrets)
  }
}

/** Host plugin body: mount the service. */
export function apply(ctx: Context): void {
  ctx.plugin(BalanceService)
}

export default BalanceService
