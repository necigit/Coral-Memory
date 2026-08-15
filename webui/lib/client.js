/**
 * 脑珊瑚 GUI 插件 浏览器半部（作者：Mr. Code Muggle @Ne，全部原创）。
 *
 * 在 DSH 设置里注册独立页面「脑珊瑚 Coral」（settings.section slot）：
 *   - 窗口A 统计：缓存占用 / 按天分布 / 即将被淘汰 Top5 / 文件路径（数据来自 /_dsh/coral/api report）
 *   - 窗口B 设置：容量/检索/配额等 7 项（点「保存」才弹确认，取消不应用；「恢复默认」二次确认）
 *
 * 数据通道：同源 fetch('/_dsh/coral/api')，由 host 半部转发给 webui/bridge.py。
 * 纯 JS 直写、零构建：产物直接就是 lib/client.js（__ModuleLoader__ 闭包格式，
 * 与 DSH 客户端插件加载约定一致，见 packages/client/tsdown.client.ts 的产物约定）。
 */
window.__ModuleLoader__.load({ id: '@dsh-external/dsh-client-coral', factory: (require) => {
var module = { exports: {} }; var exports = module.exports;
'use strict'

const React = require('react')
const { useState, useEffect, useCallback } = React
const h = React.createElement

const API = '/_dsh/coral/api'
const SECTION_ID = 'coral'

// ---------- 工具 ----------
async function callApi(action, args) {
  const res = await fetch(API, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ action, args: args || {} }),
  })
  return res.json()
}

const CSS = {
  field: { display: 'grid', gap: '5px', alignContent: 'start', minWidth: 0 },
  label: { fontSize: '11px', color: 'var(--dsw-alias-fg-muted, #77736d)', fontWeight: 600 },
  input: {
    padding: '7px 9px', borderRadius: '8px', border: '1px solid var(--dsw-alias-border-subtle, #dedbd5)',
    background: 'var(--dsw-alias-bg-layer-2, #f7f5f1)', color: 'inherit', font: 'inherit',
    fontSize: '13px', width: '100%', boxSizing: 'border-box',
  },
  btn: { padding: '8px 14px', borderRadius: '999px', border: '0', cursor: 'pointer', fontSize: '12px', fontWeight: 650 },
  primary: { padding: '8px 14px', borderRadius: '999px', border: '0', cursor: 'pointer', fontSize: '12px', fontWeight: 650, background: '#6758d4', color: '#fff' },
  ghost: {
    padding: '8px 14px', borderRadius: '999px', cursor: 'pointer', fontSize: '12px', fontWeight: 650,
    background: 'transparent', color: 'inherit', border: '1px solid var(--dsw-alias-border-subtle, #dedbd5)',
  },
  card: {
    padding: '14px 16px', border: '1px solid var(--dsw-alias-border-subtle, #dedbd5)',
    borderRadius: '14px', background: 'var(--dsw-alias-bg-layer-1, #fff)', display: 'grid', gap: '10px',
  },
  muted: { color: 'var(--dsw-alias-fg-muted, #77736d)' },
}

function Panel({ title, sub, children }) {
  return h('div', { style: CSS.card },
    h('div', {},
      h('h3', { style: { margin: '0', fontSize: '14px' } }, title),
      sub ? h('p', { style: { margin: '3px 0 0', fontSize: '11px', ...CSS.muted } }, sub) : null),
    children)
}

function Metric({ label, value }) {
  return h('div', { style: { padding: '10px 12px', borderRadius: '10px', background: 'var(--dsw-alias-bg-layer-2, #f7f5f1)', display: 'grid', gap: '3px' } },
    h('span', { style: { fontSize: '10px', textTransform: 'uppercase', letterSpacing: '.06em', ...CSS.muted } }, label),
    h('strong', { style: { fontSize: '14px' } }, value))
}

// ---------- 窗口A：统计 ----------
function Histogram({ buckets, earlier }) {
  const days = Object.keys(buckets || {})
  const max = Math.max(1, ...Object.values(buckets || {}))
  const rows = days.map((day) => {
    const bar = h('div', {
      style: {
        height: '100%', width: `${Math.round((buckets[day] / max) * 100)}%`,
        background: 'linear-gradient(90deg,#8b7cf0,#6758d4)', borderRadius: '7px',
      },
    })
    const track = h('div', { style: { flex: '1', height: '14px', borderRadius: '7px', background: 'var(--dsw-alias-bg-layer-2, #f7f5f1)', overflow: 'hidden' } }, bar)
    const dayLabel = h('span', { style: { width: '74px', flex: 'none', fontSize: '10px', ...CSS.muted, fontVariantNumeric: 'tabular-nums' } }, day.slice(5))
    const count = h('span', { style: { width: '28px', textAlign: 'right', fontSize: '11px', fontVariantNumeric: 'tabular-nums' } }, buckets[day])
    return h('div', { key: day, style: { display: 'flex', alignItems: 'center', gap: '8px' } }, dayLabel, track, count)
  })
  rows.push(h('div', { key: 'earlier', style: { fontSize: '11px', ...CSS.muted } }, `更早（>14天）：${earlier || 0} 条`))
  return h('div', { style: { display: 'grid', gap: '4px' } }, rows)
}

function PathRow({ name, value }) {
  const keySpan = h('span', { style: { width: '84px', flex: 'none', ...CSS.muted } }, name)
  const code = h('code', { style: { fontSize: '10px', color: '#6659c7', wordBreak: 'break-all' } }, value)
  return h('li', { style: { fontSize: '11px', display: 'flex', gap: '8px' } }, keySpan, code)
}

function PreviewRow({ p }) {
  const content = h('span', { style: { overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' } }, p.content)
  const meta = h('span', { style: { flex: 'none', ...CSS.muted, fontVariantNumeric: 'tabular-nums' } },
    `热度 ${p.heat} · ${p.days_old}天前 · ${p.location}`)
  return h('li', {
    style: { display: 'flex', justifyContent: 'space-between', gap: '10px', padding: '7px 9px', borderRadius: '8px', background: 'var(--dsw-alias-bg-layer-2, #f7f5f1)', fontSize: '11px' },
  }, content, meta)
}

function StatsTab({ report }) {
  const st = report.stats || {}
  const du = report.disk_usage || {}
  const hist = report.day_histogram || {}
  const preview = report.eviction_preview || []
  const paths = report.paths || {}
  const metrics = [
    ['热区 hot', st.hot ?? '—'], ['温区 warm', st.warm ?? '—'], ['冷区 cold', st.cold ?? '—'],
    ['总数 total', st.total ?? '—'], ['链路 threads', st.threads ?? '—'],
    ['磁盘占用', `${((du.total || 0) / 1048576).toFixed(2)} MB`],
  ]
  const metricNodes = metrics.map(([label, value]) => h(Metric, { key: label, label, value }))
  const quotaNote = du.max_bytes > 0
    ? `磁盘配额：${(du.max_bytes / 1048576).toFixed(0)} MB，已用 ${Math.round((du.ratio || 0) * 100)}%`
    : '磁盘配额：未限制'
  const pathNodes = Object.entries(paths).map(([name, value]) => h(PathRow, { key: name, name, value }))
  const previewNodes = preview.length === 0
    ? [h('div', { key: 'empty', style: { fontSize: '12px', ...CSS.muted } }, '暂无（记忆池为空或热度均匀）')]
    : preview.map((p) => h(PreviewRow, { key: p.id, p }))

  return h('div', { style: { display: 'grid', gap: '12px', maxWidth: '900px' } },
    h(Panel, { title: '缓存占用' },
      h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(120px,1fr))', gap: '8px' } }, metricNodes),
      h('div', { style: { fontSize: '11px', ...CSS.muted } }, quotaNote)),
    h(Panel, { title: '记忆来自哪一天（近 14 天）' },
      h(Histogram, { buckets: hist.last_14_days || {}, earlier: hist.earlier || 0 })),
    h(Panel, { title: '即将被淘汰的冷记忆 Top5' },
      h('ul', { style: { listStyle: 'none', margin: '0', padding: '0', display: 'grid', gap: '5px' } }, previewNodes)),
    h(Panel, { title: '缓存文件地址' },
      h('ul', { style: { listStyle: 'none', margin: '0', padding: '0', display: 'grid', gap: '3px' } }, pathNodes)))
}

// ---------- 窗口B：设置 ----------
const SETTING_FIELDS = [
  { key: 'memory.capacity_threshold', label: '记忆容量上限', hint: '超过后先蒸馏、再淘汰最低热度' },
  { key: 'retrieval.top_k', label: '检索返回条数', hint: '每次 memory_search 返回几条' },
  { key: 'retrieval.min_score', label: '最低相关分数', hint: '0~1，低于此分的检索结果被过滤' },
  { key: 'memory.sim_threshold_hot', label: '去重相似度阈值', hint: '热区重复判定（Jaccard ≥ 此值合并）' },
  { key: 'storage.max_bytes', label: '磁盘配额（字节）', hint: '0 = 不限制' },
  { key: 'memory.hot_ttl_hours', label: '热区保留小时数', hint: '超时后进入温区' },
  { key: 'retrieval.tau_days', label: '时间衰减常数（天）', hint: '越旧的记忆得分越低' },
]

const PATH_FIELDS = [
  { key: 'warm_cache', label: '温区缓存', hint: 'coral_warm.json' },
  { key: 'cold_archive', label: '冷区存储', hint: 'coral_cold.jsonl' },
  { key: 'vector_store', label: '向量矩阵', hint: 'coral_vectors.npy' },
  { key: 'vector_index', label: '向量索引', hint: 'coral_vector_index.json' },
  { key: 'threads', label: '推理链路', hint: 'coral_threads.json' },
]

function pathValue(config, key) {
  if (key === 'threads') return (config.threads && config.threads.path) || ''
  return (config.paths && config.paths[key]) || ''
}

function fieldValue(config, key) {
  let node = config
  for (const part of key.split('.')) {
    if (node == null || typeof node !== 'object') return ''
    node = node[part]
  }
  return node
}

function SettingField({ field, value, onChange }) {
  const input = h('input', {
    style: CSS.input, type: 'number', step: 'any', value,
    onChange: (e) => onChange(e.target.value),
  })
  const hint = h('span', { style: { fontSize: '10px', ...CSS.muted } }, field.hint)
  return h('div', { style: CSS.field },
    h('label', { style: CSS.label }, field.label),
    input, hint)
}

function SettingsTab({ config, onChanged }) {
  const [draft, setDraft] = useState({})
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState(null)
  const feedbackColor = { notice: '#5149a6', error: '#aa3939', success: '#267d52' }

  const setField = (key, value) => setDraft((d) => ({ ...d, [key]: value }))

  const save = async () => {
    const changed = SETTING_FIELDS.filter((f) =>
      draft[f.key] !== undefined && String(draft[f.key]) !== String(fieldValue(config, f.key)))
    if (changed.length === 0) {
      setFeedback({ kind: 'notice', text: '没有需要保存的更改' })
      return
    }
    const lines = changed.map((f) => `• ${f.label}: ${fieldValue(config, f.key)} → ${draft[f.key]}`)
    if (!window.confirm(`确认应用以下更改？\n\n${lines.join('\n')}`)) return
    setBusy(true)
    try {
      for (const f of changed) {
        const r = await callApi('set_config', { key_path: f.key, value: draft[f.key] })
        if (!r.ok) { setFeedback({ kind: 'error', text: `${f.label} 保存失败: ${r.error}` }); return }
      }
      setFeedback({ kind: 'success', text: `已保存 ${changed.length} 项更改（热加载生效）` })
      setDraft({})
      onChanged()
    } finally {
      setBusy(false)
    }
  }

  const resetAll = async () => {
    if (!window.confirm('恢复默认配置？\n\n注意：只重置配置项，不会清空任何缓存文件（记忆数据原样保留）。')) return
    setBusy(true)
    try {
      const r = await callApi('reset_config', {})
      if (!r.ok) { setFeedback({ kind: 'error', text: `恢复失败: ${r.error}` }); return }
      setFeedback({ kind: 'success', text: '已恢复默认配置（缓存文件未动）' })
      setDraft({})
      onChanged()
    } finally {
      setBusy(false)
    }
  }

  const emb = (config && config.embedding) || {}
  const [pathDraft, setPathDraft] = useState({})
  const setPathField = (key, value) => setPathDraft((d) => ({ ...d, [key]: value }))

  const savePaths = async () => {
    const changed = {}
    for (const f of PATH_FIELDS) {
      if (pathDraft[f.key] !== undefined && String(pathDraft[f.key]) !== String(pathValue(config, f.key))) {
        changed[f.key] = pathDraft[f.key]
      }
    }
    const keys = Object.keys(changed)
    if (keys.length === 0) {
      setFeedback({ kind: 'notice', text: '没有需要保存的路径更改' })
      return
    }
    const lines = keys.map((k) => `• ${k}: ${pathValue(config, k)} → ${changed[k]}`)
    if (!window.confirm(`确认迁移缓存路径？旧文件会自动搬到新位置（数据不丢）。\n\n${lines.join('\n')}\n\n⚠️ 迁移后需重启珊瑚进程完全生效。`)) return
    setBusy(true)
    try {
      const r = await callApi('set_paths', { paths: changed })
      if (!r.ok) { setFeedback({ kind: 'error', text: `路径保存失败: ${r.error}` }); return }
      setFeedback({ kind: 'success', text: `已迁移 ${(r.moved || []).length} 个文件，重启珊瑚进程后完全生效` })
      setPathDraft({})
      onChanged()
    } finally {
      setBusy(false)
    }
  }

  const fieldNodes = SETTING_FIELDS.map((f) =>
    h(SettingField, {
      key: f.key, field: f,
      value: draft[f.key] !== undefined ? draft[f.key] : fieldValue(config, f.key),
      onChange: (v) => setField(f.key, v),
    }))
  const pathFieldNodes = PATH_FIELDS.map((f) => {
    const input = h('input', {
      style: CSS.input, type: 'text',
      value: pathDraft[f.key] !== undefined ? pathDraft[f.key] : pathValue(config, f.key),
      onChange: (e) => setPathField(f.key, e.target.value),
    })
    return h('div', { key: f.key, style: CSS.field },
      h('label', { style: CSS.label }, f.label),
      input,
      h('span', { style: { fontSize: '10px', ...CSS.muted } }, f.hint))
  })

  return h('div', { style: { display: 'grid', gap: '12px', maxWidth: '900px' } },
    h(Panel, { title: '基础设置', sub: '点「保存」才会应用更改；「恢复默认」只重置配置、不清缓存' },
      h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(240px,1fr))', gap: '12px' } }, fieldNodes),
      h('div', { style: { display: 'flex', gap: '8px', alignItems: 'center' } },
        h('button', { style: CSS.ghost, disabled: busy, onClick: resetAll }, '恢复默认'),
        h('button', { style: CSS.primary, disabled: busy, onClick: save }, busy ? '处理中…' : '保存'),
        feedback ? h('span', { style: { fontSize: '12px', color: feedbackColor[feedback.kind] } }, feedback.text) : null)),
    h(Panel, { title: '缓存路径（高级）', sub: '相对路径以珊瑚仓库根为准；保存后自动迁移旧文件，需重启珊瑚进程完全生效' },
      h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(240px,1fr))', gap: '12px' } }, pathFieldNodes),
      h('div', { style: { display: 'flex', gap: '8px', alignItems: 'center' } },
        h('button', { style: CSS.primary, disabled: busy, onClick: savePaths }, busy ? '处理中…' : '保存路径'),
        h('span', { style: { fontSize: '11px', ...CSS.muted } }, '⚠️ 修改路径会把旧文件搬到新位置，运行中的珊瑚进程需重启后切换'))),
    h(Panel, { title: '嵌入模型（只读）' },
      h('div', { style: { fontSize: '12px', display: 'grid', gap: '3px' } },
        h('div', {}, `模型：${emb.model_name || '—'} · 维度：${emb.dim ?? '—'}`),
        h('div', { style: { fontSize: '11px', ...CSS.muted } },
          '切换模型/维度会改变向量语义，必须用 migrate_bge.py 重建向量区，不能在设置页直接改。'))))
}

// ---------- 设置页主体（A/B 双子窗口） ----------
function CoralSection() {
  const [tab, setTab] = useState('stats')
  const [report, setReport] = useState(null)
  const [config, setConfig] = useState(null)
  const [error, setError] = useState(null)

  const refresh = useCallback(() => {
    callApi('report').then((r) => { if (r.ok) setReport(r); else setError(r.error) })
    callApi('get_config', {}).then((r) => { if (r.ok) setConfig(r.config); else setError(r.error) })
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const tabBtn = (id, label) => h('button', {
    style: {
      padding: '7px 16px', borderRadius: '999px', border: '0', cursor: 'pointer', font: 'inherit',
      fontSize: '13px', fontWeight: tab === id ? 700 : 500,
      background: tab === id ? 'rgba(103,88,212,.12)' : 'transparent',
      color: tab === id ? '#6758d4' : 'var(--dsw-alias-fg-muted, #77736d)',
    },
    onClick: () => setTab(id),
  }, label)

  return h('div', {
    style: { display: 'grid', gap: '14px', padding: '8px 2px 32px', color: 'var(--dsw-alias-fg-primary, #26231f)' },
  },
    h('div', { style: { display: 'flex', justifyContent: 'space-between', gap: '20px', alignItems: 'flex-start' } },
      h('div', {},
        h('div', { style: { fontSize: '10px', textTransform: 'uppercase', letterSpacing: '.1em', color: '#6758d4', fontWeight: 700 } }, 'Coral Memory'),
        h('h2', { style: { fontSize: '25px', letterSpacing: '-.025em', margin: '3px 0 6px' } }, '脑珊瑚'),
        h('p', { style: { maxWidth: '620px', margin: '0', fontSize: '13px', lineHeight: '1.55', ...CSS.muted } },
          '面向 LLM Agent 的持久化记忆层：三级存储 + 热度淘汰 + 推理线索链路。这里是缓存占用与配置的管理面板。')),
      h('div', { style: { display: 'flex', gap: '8px', alignItems: 'center' } },
        h('button', { style: CSS.ghost, onClick: refresh }, '刷新'),
        tabBtn('stats', '窗口A 统计'),
        tabBtn('settings', '窗口B 设置'))),
    error ? h('div', { style: { ...CSS.card, color: '#aa3939', fontSize: '12px' } }, `加载失败：${error}`) : null,
    tab === 'stats'
      ? (report ? h(StatsTab, { report }) : h('div', { style: { fontSize: '13px', ...CSS.muted } }, '加载中…'))
      : (config ? h(SettingsTab, { config, onChanged: refresh }) : h('div', { style: { fontSize: '13px', ...CSS.muted } }, '加载中…')))
}

// ---------- 注册 settings.section ----------
exports.inject = ['slots']
exports.apply = function apply(ctx) {
  ctx.slots.inject('settings.section', () => ctx.slots.register({
    name: 'settings.section',
    id: SECTION_ID,
    order: 25,
    label: () => '脑珊瑚 Coral',
    inject: () => ({}),
  }, CoralSection))
}

return module.exports
} })
