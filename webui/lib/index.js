/**
 * 脑珊瑚 GUI 插件 host 半部（作者：Mr. Code Muggle @Ne，全部原创）。
 *
 * 职责：注册同源路由 /_dsh/coral/api，把浏览器设置面板的请求转发给
 * webui/bridge.py（spawn python，复用 three_dog_coral 的 report/config_* 逻辑）。
 *
 * 仅使用 DSH 公开插件 API（webServer 服务 + effect 生命周期），不依赖任何
 * DSH 内部实现；模式参考 DSH 公开插件文档中 webServer.register 的用法
 * （参考：packages/dsh-vision-toolkit/src/web.ts 的同源路由写法）。
 *
 * 安装后首次生效需重启 dsh web；之后改 lib/client.js 由 HMR 免刷新热更。
 */

import { execFile } from 'node:child_process'
import { fileURLToPath } from 'node:url'

export const name = '@dsh-external/dsh-client-coral'
// webServer 由 ctx.inject(['webServer'], ...) 做可选运行时注入（参考 dsh-vision-toolkit/web.ts），
// logger 是 Cordis 上下文内置服务；二者都不应出现在插件级强制 inject 中，否则会因服务名
// 大小写不匹配（webserver≠webServer）或非 web profile 缺失而永久 pending。
export const inject = []

const API_ROUTE = '/_dsh/coral/api'

function bridgePath() {
  // webui/lib/index.js -> webui/bridge.py
  return fileURLToPath(new URL('../bridge.py', import.meta.url))
}

function runBridge(action, args, callback) {
  const payload = JSON.stringify(args ?? {})
  // cwd 显式锚定到珊瑚仓库根：coral_config.json 的 paths 是相对路径，
  // 必须相对配置所在处解析（与 bridge.py 的 chdir 双保险，杜绝路径落错目录）。
  const coralRoot = fileURLToPath(new URL('../../', import.meta.url))
  execFile(
    'python',
    ['-X', 'utf8', bridgePath(), action, payload],
    { timeout: 30000, windowsHide: true, cwd: coralRoot, maxBuffer: 8 * 1024 * 1024 },
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
    runBridge(action, args, (payload) => sendJson(res, payload.ok === false ? 500 : 200, payload))
  })
}

export function apply(ctx) {
  ctx.inject(['webServer'], (webCtx) => {
    webCtx.effect(() => {
      const dispose = webCtx.webServer.register({
        kind: 'exact',
        path: API_ROUTE,
        handler: handleRequest,
      })
      ctx.logger?.info('dsh-client-coral: 路由已挂载 %s', API_ROUTE)
      return () => dispose()
    }, 'dsh-client-coral: API routes')
  })
}
