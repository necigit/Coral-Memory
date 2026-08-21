import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * BoardCapsule: a bottom-right capsule over shell.overlay showing the coral
 * reasoning-thread task board. Collapsed: one pill with active/candidate
 * counts. Expanded: the active threads (status · title · steps · age · who),
 * done-candidates highlighted. Read-only — archiving stays with the LLM
 * (coral MCP thread_archive), matching the balance capsule's philosophy.
 * Session-free, visible-only 30s poll, zero model tokens.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import css from './BoardCapsule.module.css';
/** Visible-only refresh cadence. */
const REFRESH_MS = 30_000;
const ICON = { active: '▶', interrupted: '⏸', archived: '▦' };
function ageText(ageDays) {
    if (ageDays < 1 / 24)
        return '刚刚';
    if (ageDays < 1)
        return `${Math.round(ageDays * 24)}小时前`;
    return `${ageDays}天前`;
}
export function BoardCapsule({ board }) {
    const [threads, setThreads] = useState(null);
    const [open, setOpen] = useState(false);
    const [loading, setLoading] = useState(false);
    const [notice, setNotice] = useState(null);
    const timer = useRef(null);
    const refresh = useCallback(async () => {
        setLoading(true);
        setNotice(null);
        try {
            const result = await board.list();
            if (!result.ok)
                throw new Error(result.error.message);
            setThreads(result.value);
        }
        catch (error) {
            setNotice(error instanceof Error ? error.message : String(error));
        }
        finally {
            setLoading(false);
        }
    }, [board]);
    // Initial load, then a visible-only poll; refocus also refreshes.
    useEffect(() => { void refresh(); }, [refresh]);
    useEffect(() => {
        const tick = () => { if (!document.hidden)
            void refresh(); };
        timer.current = window.setInterval(tick, REFRESH_MS);
        const onVisible = () => { if (!document.hidden)
            void refresh(); };
        document.addEventListener('visibilitychange', onVisible);
        return () => {
            if (timer.current !== null)
                window.clearInterval(timer.current);
            document.removeEventListener('visibilitychange', onVisible);
        };
    }, [refresh]);
    const active = threads?.filter(t => t.status === 'active') ?? [];
    const candidates = active.filter(t => t.isCandidate);
    const archivedN = threads?.filter(t => t.status === 'archived').length ?? 0;
    return (_jsxs("div", { className: css.stack, "data-coral-board": true, children: [loading && _jsx("div", { className: css.spinner, "aria-label": "\u5237\u65B0\u4E2D" }), notice !== null && _jsx("div", { className: css.notice, role: "alert", children: notice }), _jsxs("button", { type: "button", className: css.pill, onClick: () => {
                    const next = !open;
                    setOpen(next);
                    if (next)
                        void refresh(); // 点开即刷新，看到最新任务状态
                }, title: open ? '收起任务表' : '展开珊瑚任务表（点开即刷新）', children: [_jsx("span", { className: open ? css.dotOk : css.dotIdle, "aria-hidden": true }), _jsxs("span", { className: css.pillLabel, children: ["\u73CA\u745A\u4EFB\u52A1 \u00B7 ", active.length, "\u6D3B\u8DC3 / ", candidates.length, "\u5019\u9009"] })] }), open && (_jsxs("div", { className: css.panel, "data-coral-board-panel": true, children: [_jsxs("div", { className: css.panelHeader, children: [_jsx("span", { className: css.panelTitle, children: "\u63A8\u7406\u7EBF\u7D22\u4EFB\u52A1\u8868" }), _jsxs("span", { className: css.panelHint, children: ["\u5DF2\u5F52\u6863 ", archivedN] })] }), _jsxs("div", { className: css.body, children: [active.length === 0 && _jsx("div", { className: css.empty, children: "\u6CA1\u6709\u6D3B\u8DC3\u7EBF\u7D22" }), active.map(t => (_jsxs("div", { className: t.isCandidate ? `${css.row} ${css.rowCand}` : css.row, title: `${t.title} — ${t.summary}`, children: [_jsx("span", { className: css.icon, children: ICON[t.status] ?? '·' }), _jsx("span", { className: css.rowTitle, children: t.title }), t.isCandidate && _jsx("span", { className: css.cand, children: "\u25CF\u5F52\u6863?" }), t.steps > 0 && (_jsx("span", { className: css.bar, title: `${t.doneSteps}/${t.steps} 步已完成`, children: _jsx("span", { className: css.barFill, style: { width: `${Math.min(100, Math.round((t.doneSteps / t.steps) * 100))}%` } }) })), _jsxs("span", { className: css.rowMeta, children: [t.doneSteps, "/", t.steps, "\u6B65 \u00B7 ", ageText(t.ageDays), " \u00B7 ", t.lastAdvanceBy || '—'] })] }, t.id)))] }), _jsx("div", { className: css.footer, children: "\u5B8C\u5DE5\u5019\u9009 \u2192 \u8BA9 LLM \u6267\u884C thread_archive <id>" })] }))] }));
}
//# sourceMappingURL=BoardCapsule.js.map