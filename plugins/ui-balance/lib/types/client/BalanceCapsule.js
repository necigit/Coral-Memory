import { jsx as _jsx, jsxs as _jsxs } from "react/jsx-runtime";
/**
 * BalanceCapsule: a bottom-right vertical stack of pills — ONE pill per
 * configured API, each showing its own balance/status. Management (add /
 * remove) is intentionally NOT in the UI: the user drives it through the
 * LLM, which mutates the store via the balance service / management script.
 * Clicking an entry pill opens a small read-only detail panel (full
 * error/balance text). Session-free root scope — local React state fed by
 * `ctx.remote.balance.*`, refreshed on a visible-only timer.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { IconCloseOutline16, Tooltip } from '@deepseek-ai/dsh-client-ui-primitives';
import css from './BalanceCapsule.module.css';
/** Visible-only refresh cadence. */
const REFRESH_MS = 60_000;
/** One currency bucket rendered as "223.23 CNY". */
function formatInfo(info) {
    return `${info.total} ${info.currency}`;
}
/** Aggregate the successful buckets of one result into a display line. */
function resultLine(result) {
    if (!result.ok)
        return result.error ?? '查询失败';
    const infos = result.infos;
    if (infos === undefined || infos.length === 0)
        return '余额为空';
    return infos.map(formatInfo).join(' · ');
}
/** Status dot color: green ok, red error, neutral while unknown. */
function toneOf(result) {
    if (result === undefined)
        return 'idle';
    return result.ok ? 'ok' : 'err';
}
export function BalanceCapsule({ balance }) {
    const [entries, setEntries] = useState(null);
    const [results, setResults] = useState(new Map());
    const [selectedId, setSelectedId] = useState(null);
    const [loading, setLoading] = useState(false);
    const [notice, setNotice] = useState(null);
    const timer = useRef(null);
    const refresh = useCallback(async () => {
        setLoading(true);
        setNotice(null);
        try {
            const listResult = await balance.list();
            if (!listResult.ok)
                throw new Error(listResult.error.message);
            setEntries(listResult.value);
            const queryResult = await balance.query();
            if (!queryResult.ok)
                throw new Error(queryResult.error.message);
            setResults(new Map(queryResult.value.map(result => [result.id, result])));
        }
        catch (error) {
            setNotice(error instanceof Error ? error.message : String(error));
        }
        finally {
            setLoading(false);
        }
    }, [balance]);
    // Initial load, then a visible-only poll; refocus also refreshes.
    useEffect(() => {
        void refresh();
    }, [refresh]);
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
    const selected = entries?.find(entry => entry.id === selectedId);
    return (_jsxs("div", { className: css.stack, "data-balance-capsule": true, children: [loading && _jsx("div", { className: css.spinner, "data-balance-loading": true, "aria-label": "\u5237\u65B0\u4E2D" }), notice !== null && _jsx("div", { className: css.notice, role: "alert", children: notice }), selected !== undefined && (_jsxs("div", { className: css.panel, "data-balance-detail": selected.id, children: [_jsxs("div", { className: css.panelHeader, children: [_jsx("span", { className: css.panelTitle, children: selected.label }), _jsx("div", { className: css.panelActions, children: _jsx(Tooltip, { label: "\u5173\u95ED", side: "top", delayMs: 400, children: _jsx("button", { type: "button", className: css.iconBtn, onClick: () => { setSelectedId(null); }, "aria-label": "\u5173\u95ED", children: _jsx(IconCloseOutline16, { size: 14 }) }) }) })] }), _jsx("div", { className: css.detailBody, children: results.get(selected.id) === undefined
                            ? '查询中…'
                            : resultLine(results.get(selected.id)) })] })), entries?.map(entry => {
                const result = results.get(entry.id);
                const tone = toneOf(result);
                const toneClass = tone === 'ok' ? css.dotOk : tone === 'err' ? css.dotErr : css.dotIdle;
                const text = result === undefined
                    ? `${entry.label} …`
                    : result.ok
                        ? `${entry.label} ${resultLine(result)}`
                        : `${entry.label} ⚠ ${result.error ?? '查询失败'}`;
                return (_jsxs("button", { type: "button", className: css.pill, "data-balance-entry": entry.id, onClick: () => { setSelectedId(prev => prev === entry.id ? null : entry.id); }, title: text, children: [_jsx("span", { className: toneClass, "aria-hidden": true }), _jsx("span", { className: css.pillLabel, children: text })] }, entry.id));
            }), entries !== null && entries.length === 0 && (_jsx("div", { className: css.pill, "data-balance-empty": true, children: "\u672A\u914D\u7F6E API" }))] }));
}
//# sourceMappingURL=BalanceCapsule.js.map