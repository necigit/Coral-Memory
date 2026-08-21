/**
 * BalanceCapsule: a bottom-right vertical stack of pills — ONE pill per
 * configured API, each showing its own balance/status. Management (add /
 * remove) is intentionally NOT in the UI: the user drives it through the
 * LLM, which mutates the store via the balance service / management script.
 * Clicking an entry pill opens a small read-only detail panel (full
 * error/balance text). Session-free root scope — local React state fed by
 * `ctx.remote.balance.*`, refreshed on a visible-only timer.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { IconCloseOutline16, Tooltip } from '@deepseek-ai/dsh-client-ui-primitives'
import type { PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
import type { BalanceEntry, BalanceInfo, BalanceResult } from '../types.ts'
import type { BalanceRemoteApi } from './remote.ts'
import css from './BalanceCapsule.module.css'

/** Visible-only refresh cadence. */
const REFRESH_MS = 60_000

export type BalanceCapsuleProps = PropsRuntime<'shell.overlay'> & { balance: BalanceRemoteApi }

/** One currency bucket rendered as "223.23 CNY". */
function formatInfo(info: BalanceInfo): string {
  return `${info.total} ${info.currency}`
}

/** Aggregate the successful buckets of one result into a display line. */
function resultLine(result: BalanceResult): string {
  if (!result.ok) return result.error ?? '查询失败'
  const infos = result.infos
  if (infos === undefined || infos.length === 0) return '余额为空'
  return infos.map(formatInfo).join(' · ')
}

/** Status dot color: green ok, red error, neutral while unknown. */
function toneOf(result: BalanceResult | undefined): 'ok' | 'err' | 'idle' {
  if (result === undefined) return 'idle'
  return result.ok ? 'ok' : 'err'
}

export function BalanceCapsule({ balance }: BalanceCapsuleProps) {
  const [entries, setEntries] = useState<BalanceEntry[] | null>(null)
  const [results, setResults] = useState<Map<string, BalanceResult>>(new Map())
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const timer = useRef<number | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setNotice(null)
    try {
      const listResult = await balance.list()
      if (!listResult.ok) throw new Error(listResult.error.message)
      setEntries(listResult.value)
      const queryResult = await balance.query()
      if (!queryResult.ok) throw new Error(queryResult.error.message)
      setResults(new Map(queryResult.value.map(result => [result.id, result])))
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error))
    } finally {
      setLoading(false)
    }
  }, [balance])

  // Initial load, then a visible-only poll; refocus also refreshes.
  useEffect(() => {
    void refresh()
  }, [refresh])

  useEffect(() => {
    const tick = (): void => { if (!document.hidden) void refresh() }
    timer.current = window.setInterval(tick, REFRESH_MS)
    const onVisible = (): void => { if (!document.hidden) void refresh() }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      if (timer.current !== null) window.clearInterval(timer.current)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [refresh])

  const selected = entries?.find(entry => entry.id === selectedId)

  return (
    <div className={css.stack} data-balance-capsule>
      {/* Loading spinner: absolutely positioned so it never shifts the pill
          stack — no layout jump on refresh, just a smooth circular indicator. */}
      {loading && <div className={css.spinner} data-balance-loading aria-label="刷新中" />}

      {notice !== null && <div className={css.notice} role="alert">{notice}</div>}

      {selected !== undefined && (
        <div className={css.panel} data-balance-detail={selected.id}>
          <div className={css.panelHeader}>
            <span className={css.panelTitle}>{selected.label}</span>
            <div className={css.panelActions}>
              <Tooltip label="关闭" side="top" delayMs={400}>
                <button type="button" className={css.iconBtn} onClick={() => { setSelectedId(null) }} aria-label="关闭">
                  <IconCloseOutline16 size={14} />
                </button>
              </Tooltip>
            </div>
          </div>
          <div className={css.detailBody}>
            {results.get(selected.id) === undefined
              ? '查询中…'
              : resultLine(results.get(selected.id) as BalanceResult)}
          </div>
        </div>
      )}

      {entries?.map(entry => {
        const result = results.get(entry.id)
        const tone = toneOf(result)
        const toneClass = tone === 'ok' ? css.dotOk : tone === 'err' ? css.dotErr : css.dotIdle
        const text = result === undefined
          ? `${entry.label} …`
          : result.ok
            ? `${entry.label} ${resultLine(result)}`
            : `${entry.label} ⚠ ${result.error ?? '查询失败'}`
        return (
          <button
            type="button"
            key={entry.id}
            className={css.pill}
            data-balance-entry={entry.id}
            onClick={() => { setSelectedId(prev => prev === entry.id ? null : entry.id) }}
            title={text}
          >
            <span className={toneClass} aria-hidden />
            <span className={css.pillLabel}>{text}</span>
          </button>
        )
      })}
      {entries !== null && entries.length === 0 && (
        <div className={css.pill} data-balance-empty>未配置 API</div>
      )}
    </div>
  )
}
