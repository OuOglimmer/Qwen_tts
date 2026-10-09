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

async function handle<T>(response: Response): Promise<T> {
  const payload: unknown = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = (payload as { detail?: unknown } | null)?.detail
    throw new Error(typeof detail === 'string' ? detail : `API ${response.status}`)
  }
  return payload as T
}

export async function apiGet<T>(path: string): Promise<T> {
  return handle<T>(await fetch(`${API_BASE}${path}`, { cache: 'no-store' }))
}

async function apiSend<T>(path: string, method: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method,
    headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  return handle<T>(response)
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
  return handle<T>(await fetch(`${API_BASE}${path}`, { method: 'POST', body: form }))
}

/** 后端返回站内相对路径（/api/files/...）时补上 base，外部 URL 原样返回。 */
export function fileUrl(url: string | null | undefined): string | null {
  if (!url) return null
  return url.startsWith('/') ? `${API_BASE}${url}` : url
}
