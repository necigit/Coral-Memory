/**
 * BoardCapsule: a bottom-right capsule over shell.overlay showing the coral
 * reasoning-thread task board. Collapsed: one pill with active/candidate
 * counts. Expanded: the active threads (status · title · steps · age · who),
 * done-candidates highlighted. Read-only — archiving stays with the LLM
 * (coral MCP thread_archive), matching the balance capsule's philosophy.
 * Session-free, visible-only 30s poll, zero model tokens.
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import type { PropsRuntime } from '@deepseek-ai/dsh-client-ui-slots'
import type { BoardThread } from '../types.ts'
import type { BoardRemoteApi } from './remote.ts'
import css from './BoardCapsule.module.css'

/** Visible-only refresh cadence. */
const REFRESH_MS = 30_000

export type BoardCapsuleProps = PropsRuntime<'shell.overlay'> & { board: BoardRemoteApi }

const ICON: Record<string, string> = { active: '▶', interrupted: '⏸', archived: '▦' }

function ageText(ageDays: number): string {
  if (ageDays < 1 / 24) return '刚刚'
  if (ageDays < 1) return `${Math.round(ageDays * 24)}小时前`
  return `${ageDays}天前`
}

export function BoardCapsule({ board }: BoardCapsuleProps) {
  const [threads, setThreads] = useState<BoardThread[] | null>(null)
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const timer = useRef<number | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    setNotice(null)
    try {
      const result = await board.list()
      if (!result.ok) throw new Error(result.error.message)
      setThreads(result.value)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : String(error))
    } finally {
      setLoading(false)
    }
  }, [board])

  // Initial load, then a visible-only poll; refocus also refreshes.
  useEffect(() => { void refresh() }, [refresh])
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

  const active = threads?.filter(t => t.status === 'active') ?? []
  const candidates = active.filter(t => t.isCandidate)
  const archivedN = threads?.filter(t => t.status === 'archived').length ?? 0

  return (
    <div className={css.stack} data-coral-board>
      {loading && <div className={css.spinner} aria-label="刷新中" />}
      {notice !== null && <div className={css.notice} role="alert">{notice}</div>}

      <button
        type="button"
        className={css.pill}
        onClick={() => {
          const next = !open
          setOpen(next)
          if (next) void refresh()  // 点开即刷新，看到最新任务状态
        }}
        title={open ? '收起任务表' : '展开珊瑚任务表（点开即刷新）'}
      >
        <span className={open ? css.dotOk : css.dotIdle} aria-hidden />
        <span className={css.pillLabel}>珊瑚任务 · {active.length}活跃 / {candidates.length}候选</span>
      </button>

      {open && (
        <div className={css.panel} data-coral-board-panel>
          <div className={css.panelHeader}>
            <span className={css.panelTitle}>推理线索任务表</span>
            <span className={css.panelHint}>已归档 {archivedN}</span>
          </div>
          <div className={css.body}>
            {active.length === 0 && <div className={css.empty}>没有活跃线索</div>}
            {active.map(t => (
              <div key={t.id} className={t.isCandidate ? `${css.row} ${css.rowCand}` : css.row}
                   title={`${t.title} — ${t.summary}`}>
                <span className={css.icon}>{ICON[t.status] ?? '·'}</span>
                <span className={css.rowTitle}>{t.title}</span>
                {t.isCandidate && <span className={css.cand}>●归档?</span>}
                {t.steps > 0 && (
                  <span className={css.bar} title={`${t.doneSteps}/${t.steps} 步已完成`}>
                    <span className={css.barFill} style={{ width: `${Math.min(100, Math.round((t.doneSteps / t.steps) * 100))}%` }} />
                  </span>
                )}
                <span className={css.rowMeta}>{t.doneSteps}/{t.steps}步 · {ageText(t.ageDays)} · {t.lastAdvanceBy || '—'}</span>
              </div>
            ))}
          </div>
          <div className={css.footer}>完工候选 → 让 LLM 执行 thread_archive &lt;id&gt;</div>
        </div>
      )}
    </div>
  )
}
