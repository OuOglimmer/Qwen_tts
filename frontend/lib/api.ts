// 传输层：只负责 HTTP 细节（base 地址、错误归一化、序列化），不理解任何业务类型。
// 领域类型见 lib/types.ts。

function resolveApiBase(): string {
  const configured = process.env.NEXT_PUBLIC_API_BASE
  if (configured) return configured
  // 默认同源相对路径：/api/* 由 next.config.mjs 的 rewrites 代理到本机 7871，
  // 浏览器只依赖 3000 端口——本机与局域网同事的访问行为完全一致。
  return ''
}

const API_BASE = resolveApiBase()

// 共享 token 门：后端设了 QWEN_TTS_AUTH_TOKEN 时，写请求必须带同一把 token。
// 前端用 NEXT_PUBLIC_AUTH_TOKEN（构建时注入 bundle）透传；两者配成同一个值。
// token 暴露在 bundle 里是设计内——威胁模型是防局域网外的随机扫描 / 跨站请求，
// 不是防「有权限访问页面的局域网同事」（他们要打开页面就得有这把 token）。
const AUTH_TOKEN = process.env.NEXT_PUBLIC_AUTH_TOKEN ?? ''

function authHeaders(extra?: Record<string, string>): Record<string, string> | undefined {
  if (!AUTH_TOKEN) return extra
  return { ...(extra ?? {}), 'X-Auth-Token': AUTH_TOKEN }
}

type ProjectWriteListener = (projectId: string | null) => void
const writeListeners = new Set<ProjectWriteListener>()

/** 订阅"项目数据被真正改动了"（null 表示改的是当前项目，调用方自己补 id）。
 *  侧栏据此才把该项目挪到列表第一位。返回取消订阅函数。 */
export function onProjectWrite(listener: ProjectWriteListener): () => void {
  writeListeners.add(listener)
  return () => { writeListeners.delete(listener) }
}

const PROJECT_SCOPED_WRITE = /^\/api\/projects\/([^/?]+)/
// 声线请求（步骤 1 的工作台）路径里不带项目 id，操作的仍是当前项目。
const CURRENT_PROJECT_WRITE = /^\/api\/voice-requests\//
// /activate 与 /last-step 只是"看到哪儿"的书签，切项目、翻步骤都会打，不算改动项目内容。
const BOOKMARK_WRITE = /\/(activate|last-step)$/

function noteProjectWrite(path: string): void {
  // 写操作一律让传输层 GET 缓存整体失效。缓存本意只是"页面重挂载时秒开"，
  // 但写之后紧跟的那次刷新若命中 5 秒内的旧快照，表现就是——后端已成功、
  // 界面毫无变化（暂存/移出、选定、删除槽位、改名全都踩过这个坑）。
  // 缓存条目只有几十个，整体清空比按路径前缀匹配更可靠。
  GET_CACHE.clear()
  const pathname = path.split('?')[0]
  if (BOOKMARK_WRITE.test(pathname)) return
  const scoped = pathname.match(PROJECT_SCOPED_WRITE)
  if (scoped) {
    const projectId = decodeURIComponent(scoped[1])
    for (const listener of writeListeners) listener(projectId)
    return
  }
  if (CURRENT_PROJECT_WRITE.test(pathname)) {
    for (const listener of writeListeners) listener(null)
  }
}

async function handle<T>(response: Response): Promise<T> {
  const payload: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = (payload as { detail?: unknown } | null)?.detail
    throw new Error(typeof detail === 'string' ? detail : `API ${response.status}`)
  }
  return payload as T
}

// 传输层 GET 结果缓存（stale-while-revalidate 的边界实现）。
// 用途：组件重挂载（切走再切回、流程步骤切换、换项目后切回同一项目）时，命中新鲜缓存可
// 秒返回、不转圈、不重复打后端；实时轮询统一带 force:true 绕过缓存以保证进度新鲜。
type GetCacheEntry = { data: unknown; at: number }
const GET_CACHE = new Map<string, GetCacheEntry>()
const DEFAULT_GET_TTL = 5000

export async function apiGet<T>(path: string, opts?: { ttl?: number; force?: boolean }): Promise<T> {
  const ttl = opts?.ttl ?? DEFAULT_GET_TTL
  if (!opts?.force) {
    const hit = GET_CACHE.get(path)
    if (hit && Date.now() - hit.at < ttl) return hit.data as T
  }
  const data = await handle<T>(await fetch(`${API_BASE}${path}`, { cache: 'no-store' }))
  GET_CACHE.set(path, { data, at: Date.now() })
  return data
}

async function apiSend<T>(path: string, method: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers: authHeaders(body === undefined ? undefined : { 'Content-Type': 'application/json' }),
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const result = await handle<T>(response)
  noteProjectWrite(path)
  return result
}

export function apiPost<T>(path: string, body?: unknown): Promise<T> {
  return apiSend<T>(path, 'POST', body)
}

export function apiPut<T>(path: string, body?: unknown): Promise<T> {
  return apiSend<T>(path, 'PUT', body)
}

export function apiDelete<T>(path: string): Promise<T> {
  return apiSend<T>(path, 'DELETE')
}

export async function apiUpload<T>(path: string, form: FormData): Promise<T> {
  const result = await handle<T>(await fetch(`${API_BASE}${path}`, { method: 'POST', body: form, headers: authHeaders() }))
  // 上传同样是写操作：声线库/转换记录都要在下一次读取时看到新行。
  noteProjectWrite(path)
  return result
}

/** POST JSON 并取回二进制流（ZIP 打包下载）。apiPost 会把响应当 JSON 解析，
 *  流式响应必须走这里，否则一解就炸。 */
export async function apiPostBlob(path: string, body: unknown): Promise<Blob> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    const payload: unknown = await response.json().catch(() => null)
    const detail = (payload as { detail?: unknown } | null)?.detail
    throw new Error(typeof detail === 'string' ? detail : `API ${response.status}`)
  }
  return response.blob()
}

/** 触发一次浏览器下载（本地生成的 Blob）。 */
export function saveBlob(blob: Blob, filename: string): void {
  const href = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = href
  link.download = filename
  link.rel = 'noopener'
  document.body.appendChild(link)
  link.click()
  link.remove()
  // 同步 revoke 会在部分浏览器（Firefox/Safari）里把还没开始的下载掐断：
  // 下载是异步启动的，链接撤得太早等于把 blob 从脚下抽走。延后一拍再释放。
  setTimeout(() => URL.revokeObjectURL(href), 1000)
}

/** 后端返回相对路径（/api/files/...）时补上 base，外部 URL 原样返回。 */
export function fileUrl(url: string | null | undefined): string | null {
  if (!url) return null
  return url.startsWith('/') ? `${API_BASE}${url}` : url
}
