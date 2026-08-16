/**
 * 脑珊瑚 Coral Memory host 半部（作者：Mr. Code Muggle @Ne，全部原创）。
 *
 * 职责（coral-memory bundle 的 host 侧）：
 * 1. provide `coralPaths` 服务：MCP 行（cordis.patch.yml 的 mcp-coral）inject 它，
 *    用 !!js 表达式读取 Python 解释器、server 脚本路径与数据目录——安装零配置。
 * 2. 注册同源路由 /_dsh/coral/api，把浏览器设置面板的请求转发给
 *    webui/bridge.py（spawn python，复用 three_dog_coral 的 report/config_* 逻辑）。
 * 3. 首次激活时自检 Python 运行时依赖（numpy / sentence-transformers），
 *    缺失时按 DSH_CORAL_AUTO_INSTALL 决定自动安装或给出可操作提示。
 *
 * 仅使用 DSH 公开插件 API（webServer 服务 + effect 生命周期），不依赖任何
 * DSH 内部实现；模式参考 DSH 公开插件文档中 webServer.register 的用法
 * （参考：packages/dsh-vision-toolkit/src/web.ts 的同源路由写法）。
 *
 * 安装后首次生效需重启 dsh web；之后改 lib/client.js 由 HMR 免刷新热更。
 */

import { execFile } from 'node:child_process'
import { existsSync } from 'node:fs'
import { mkdir } from 'node:fs/promises'
import { homedir } from 'node:os'
import { fileURLToPath } from 'node:url'
import path, { join } from 'node:path'

export const name = '@dsh-external/dsh-client-coral'
// webServer 由 ctx.inject(['webServer'], ...) 做可选运行时注入（参考 dsh-vision-toolkit/web.ts），
// logger 是 Cordis 上下文内置服务；二者都不应出现在插件级强制 inject 中，否则会因服务名
// 大小写不匹配（webserver≠webServer）或非 web profile 缺失而永久 pending。
export const inject = []

const API_ROUTE = '/_dsh/coral/api'

// ── 包内路径（发布后以 npm 包形式安装，路径必须包内自解析，不依赖仓库根） ──
// lib/index.js → 包根（webui/ = 包根）→ runtime/coral_mcp_server.py、bridge.py
const PKG_ROOT = fileURLToPath(new URL('../', import.meta.url))
const RUNTIME_DIR = join(PKG_ROOT, 'runtime')
const MCP_SERVER_PATH = join(RUNTIME_DIR, 'coral_mcp_server.py')

/** 记忆数据目录：DSH_CORAL_DATA_DIR 显式覆盖 > $DSH_HOME/coral（默认）。 */
function resolveDataDir() {
  if (process.env.DSH_CORAL_DATA_DIR) return process.env.DSH_CORAL_DATA_DIR
  const home = process.env.DSH_HOME || join(homedir(), '.dsh')
  return join(home, 'coral')
}

// ── Python 解释器解析 ──────────────────────────────────────────────────────
// 背景（2026-08-15）：host 进程 spawn 裸 `python` 依赖启动环境的 PATH，在
// PATH 不含 python 的环境（如容器内未激活 venv）会报
// "bridge 失败: spawn python ENOENT"。MCP 侧（cordis.patch.yml）用绝对路径
// 所以一直正常——这里补上同样的兜底，查找顺序：
//   DSH_CORAL_PYTHON 环境变量（显式覆盖，最优先，适合 compose 里钉死 venv）
//   → VIRTUAL_ENV（已激活的 venv）
//   → 包旁/仓库旁的 venv（<pkgRoot>/.venv，容器部署时 venv 通常就建在包旁）
//   → PATH 逐目录扫描 python(.exe) / python3(.exe)
//   → 常见安装路径（Windows 安装 / 容器 venv / 系统 python）
//   → Windows py 启动器（py -3）
// 结果进程内缓存；找不到时给出可操作的报错而不是裸 ENOENT。
const KNOWN_PYTHON_PATHS = [
  // Windows 常见安装位置
  'C:/Python313/python.exe',
  'C:/Python312/python.exe',
  'C:/Python311/python.exe',
  'C:/Python310/python.exe',
  'C:/Program Files/Python313/python.exe',
  'C:/Program Files/Python312/python.exe',
  'C:/Program Files/Python311/python.exe',
  // Linux / Docker 容器常见 venv 与系统 python
  '/opt/venv/bin/python',
  '/venv/bin/python',
  '/app/venv/bin/python',
  '/usr/local/bin/python3',
  '/usr/bin/python3',
  '/usr/local/bin/python',
  '/usr/bin/python',
]

let cachedPython // undefined=未探测；null=没找到；否则 { cmd, args }

export function resetPythonCache() {
  cachedPython = undefined
}

export function resolvePython() {
  if (cachedPython !== undefined) return cachedPython

  const isWin = process.platform === 'win32'
  const pyName = (n) => (isWin ? `${n}.exe` : n)
  const pathDirs = (process.env.PATH ?? '')
    .split(path.delimiter)
    .map((d) => d.trim().replace(/^"+|"+$/g, ''))
    .filter(Boolean)

  // 1) 显式覆盖
  const override = process.env.DSH_CORAL_PYTHON
  if (override) {
    cachedPython = { cmd: override, args: [] }
    return cachedPython
  }

  // 2) 已激活的 venv
  const venv = process.env.VIRTUAL_ENV
  if (venv) {
    const venvPy = path.join(venv, isWin ? 'Scripts/python.exe' : 'bin/python')
    try {
      if (existsSync(venvPy)) {
        cachedPython = { cmd: venvPy, args: [] }
        return cachedPython
      }
    } catch { /* 目录不可读等，继续往下找 */ }
  }

  // 3) 包旁/仓库旁的 venv（容器部署时 venv 通常就建在包旁）
  const repoVenvPy = path.join(PKG_ROOT, '.venv', isWin ? 'Scripts/python.exe' : 'bin/python')
  try {
    if (existsSync(repoVenvPy)) {
      cachedPython = { cmd: repoVenvPy, args: [] }
      return cachedPython
    }
  } catch { /* 继续 */ }

  // 4) PATH 逐目录扫描
  for (const name of isWin ? ['python', 'python3'] : ['python3', 'python']) {
    for (const dir of pathDirs) {
      // 跳过 WindowsApps 的 python3 App Execution Alias（可能是打开商店的占位 stub）
      if (name === 'python3' && /windowsapps/i.test(dir)) continue
      const candidate = path.join(dir, pyName(name))
      try {
        if (existsSync(candidate)) {
          cachedPython = { cmd: candidate, args: [] }
          return cachedPython
        }
      } catch { /* 无权限等，跳过 */ }
    }
  }

  // 5) 常见安装路径兜底
  for (const candidate of KNOWN_PYTHON_PATHS) {
    try {
      if (existsSync(candidate)) {
        cachedPython = { cmd: candidate, args: [] }
        return cachedPython
      }
    } catch { /* 跳过 */ }
  }

  // 6) Windows py 启动器
  if (isWin) {
    for (const dir of [...pathDirs, 'C:/Windows', 'C:/Windows/System32']) {
      const candidate = path.join(dir, 'py.exe')
      try {
        if (existsSync(candidate)) {
          cachedPython = { cmd: candidate, args: ['-3'] }
          return cachedPython
        }
      } catch { /* 跳过 */ }
    }
  }

  cachedPython = null
  return cachedPython
}

function bridgePath() {
  // lib/index.js -> bridge.py
  return fileURLToPath(new URL('../bridge.py', import.meta.url))
}

function runBridge(action, args, callback, env = {}) {
  const payload = JSON.stringify(args ?? {})
  const dataDir = resolveDataDir()
  // cwd 显式锚定到记忆数据目录：coral_config.json 的 paths 是相对路径，
  // 必须相对配置所在处解析（与 bridge.py 的 chdir 双保险，杜绝路径落错目录）。
  const py = resolvePython()
  if (!py) {
    callback({
      ok: false,
      error:
        'bridge 失败: 找不到 Python 解释器（spawn python ENOENT）。' +
        '请安装 Python 3 并加入 PATH，或在启动环境里设置 DSH_CORAL_PYTHON ' +
        '指向 python 可执行文件后重启 dsh web',
    })
    return
  }
  execFile(
    py.cmd,
    [...py.args, '-X', 'utf8', bridgePath(), action, payload],
    {
      timeout: 30000,
      windowsHide: true,
      cwd: dataDir,
      env: { ...process.env, CORAL_DATA_DIR: dataDir, ...env },
      maxBuffer: 8 * 1024 * 1024,
    },
    (error, stdout) => {
      if (error) {
        callback({ ok: false, error: `bridge 失败: ${error.message}` })
        return
      }
      try {
        callback(JSON.parse(stdout))
      } catch {
        callback({ ok: false, error: 'bridge 返回非 JSON' })
      }
    },
  )
}

function sendJson(res, status, payload) {
  const bytes = Buffer.from(JSON.stringify(payload), 'utf8')
  res.setHeader('Content-Type', 'application/json; charset=utf-8')
  res.setHeader('Content-Length', String(bytes.length))
  res.setHeader('Cache-Control', 'no-store')
  res.setHeader('X-Content-Type-Options', 'nosniff')
  res.writeHead(status)
  res.end(bytes)
}

function handleRequest(req, res) {
  // 只服务同源请求：浏览器设置面板 fetch 同源路由，无需 CORS。
  // 防御：拒绝非本机回环来源（X-Forwarded 来自 dsh webserver 网关层）。
  let body = ''
  req.on('data', (chunk) => { body += chunk })
  req.on('end', () => {
    if (req.method === 'GET') {
      runBridge('report', {}, (payload) => sendJson(res, payload.ok === false ? 500 : 200, payload))
      return
    }
    if (req.method !== 'POST') {
      sendJson(res, 405, { ok: false, error: '只支持 GET/POST' })
      return
    }
    let parsed = {}
    try {
      parsed = body ? JSON.parse(body) : {}
    } catch {
      sendJson(res, 400, { ok: false, error: '请求体不是合法 JSON' })
      return
    }
    const { action, args } = parsed
    if (typeof action !== 'string' || !action) {
      sendJson(res, 400, { ok: false, error: '缺少 action' })
      return
    }
    // 其余 action 走 bridge.py（report / config_*）
    runBridge(action, args, (payload) => sendJson(res, payload.ok === false ? 500 : 200, payload))
  })
}

// ── Python 运行时依赖自检 ──────────────────────────────────────────────────
// 首次激活时探测 numpy（必需）与 sentence-transformers（可选，缺失时降级 hash 嵌入）。
// DSH_CORAL_AUTO_INSTALL=1 时自动 pip install（numpy 必装；sentence-transformers 尝试装，
// 失败只提示不报错——体积大且需要网络）；否则只输出可操作的提示日志。
let runtimeChecked = false

function probeImport(py, moduleName) {
  return new Promise((resolve) => {
    execFile(
      py.cmd,
      [...py.args, '-c', `import ${moduleName}`],
      { timeout: 30000, windowsHide: true },
      (error) => resolve(error === null),
    )
  })
}

function pipInstall(py, spec) {
  return new Promise((resolve) => {
    execFile(
      py.cmd,
      [...py.args, '-m', 'pip', 'install', '--quiet', spec],
      { timeout: 10 * 60 * 1000, windowsHide: true },
      (error) => resolve(error === null),
    )
  })
}

export function ensurePythonRuntime(ctx) {
  if (runtimeChecked) return
  runtimeChecked = true
  const py = resolvePython()
  const dataDir = resolveDataDir()
  const log = (msg) => { ctx.logger?.warn?.(msg) ?? console.warn(`[coral-memory] ${msg}`) }
  if (!py) {
    log('找不到 Python 解释器：MCP 工具与设置面板不可用。请设置 DSH_CORAL_PYTHON 指向 python 可执行文件后重启。')
    return
  }
  ;(async () => {
    try {
      await mkdir(dataDir, { recursive: true })
    } catch { /* 数据目录不可建时由 server 自行报错 */ }
    const hasNumpy = await probeImport(py, 'numpy')
    if (!hasNumpy) {
      const auto = process.env.DSH_CORAL_AUTO_INSTALL === '1'
      if (auto) {
        const ok = await pipInstall(py, 'numpy')
        log(ok
          ? `已自动安装 numpy（${dataDir}）`
          : `numpy 安装失败：请手动执行 ${py.cmd} -m pip install numpy 后重启 dsh web`)
      } else {
        log(`缺少 numpy：请执行 ${py.cmd} -m pip install numpy 后重启 dsh web（或设置 DSH_CORAL_AUTO_INSTALL=1 自动安装）`)
      }
    }
    const hasSt = await probeImport(py, 'sentence_transformers')
    if (!hasSt) {
      const auto = process.env.DSH_CORAL_AUTO_INSTALL === '1'
      if (auto) {
        const ok = await pipInstall(py, 'sentence-transformers')
        if (!ok) log('sentence-transformers 安装失败（可能网络受限）：将继续使用降级 hash 嵌入，中文语义检索效果下降。')
      } else {
        log('缺少 sentence-transformers：将使用降级 hash 嵌入（中文语义检索效果下降）。安装命令：' +
          `${py.cmd} -m pip install sentence-transformers（推荐 bge 中文模型，见 README）`)
      }
    }
  })()
}

export function apply(ctx) {
  // 提供 coralPaths 服务：cordis.patch.yml 的 mcp-coral 行 inject 本服务后，
  // 用 !!js 表达式读取 command/args/env —— 安装零配置，路径全部包内自解析。
  const py = resolvePython()
  const dataDir = resolveDataDir()
  ctx.provide('coralPaths', {
    pythonCmd: py ? py.cmd : 'python3',
    pythonArgs: py ? [...py.args, MCP_SERVER_PATH] : [MCP_SERVER_PATH],
    dataDir,
  })
  ensurePythonRuntime(ctx)

  ctx.inject(['webServer'], (webCtx) => {
    webCtx.effect(() => {
      const dispose = webCtx.webServer.register({
        kind: 'exact',
        path: API_ROUTE,
        handler: handleRequest,
      })
      // crypto.randomUUID polyfill：该 API 只在安全上下文（HTTPS/localhost）可用，
      // HTTP+LAN IP 访问时工作区选择等核心功能会报 "crypto.randomUUID is not a function"。
      // 用 getRandomValues（任何上下文可用）等价实现注入页面；安全上下文下脚本自检为空操作。
      const disposeTap = webCtx.webServer.tapIndex((html) => {
        if (html.includes('coral-randomuuid-polyfill')) return html
        const polyfill = '<script id="coral-randomuuid-polyfill">' +
          'if(typeof crypto!==\'undefined\'&&!crypto.randomUUID){' +
          'crypto.randomUUID=function(){var b=crypto.getRandomValues(new Uint8Array(16));' +
          'b[6]=b[6]&15|64;b[8]=b[8]&63|128;' +
          'var h=Array.from(b,function(x){return x.toString(16).padStart(2,"0")});' +
          'return h[0]+h[1]+h[2]+h[3]+"-"+h[4]+h[5]+"-"+h[6]+h[7]+"-"+h[8]+h[9]+"-"+h[10]+h[11]+h[12]+h[13]+h[14]+h[15]};}' +
          '</script>'
        return html.replace('</head>', polyfill + '\n</head>')
      })
      ctx.logger?.info('coral-memory: 路由已挂载 %s + crypto.randomUUID polyfill 已注入', API_ROUTE)
      return () => {
        dispose()
        disposeTap()
      }
    }, 'coral-memory: API routes + index polyfill')
  })
}
