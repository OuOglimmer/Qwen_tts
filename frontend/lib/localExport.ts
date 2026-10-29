// 浏览器直写本机文件夹：导出发生在「打开页面的那台电脑」，而不是跑后端的主机。
// File System Access API 只在安全上下文（https 或 localhost）可用，调用前必须先用
// supportsDirectoryExport 判断，不支持时上层退回 ZIP 下载。
import { apiGet, fileUrl } from '@/lib/api'
import type { ExportPlanResponse } from '@/lib/types'

type FileHandle = {
  getFile(): Promise<Blob>
  createWritable(): Promise<{ write(data: Blob | string): Promise<void>; close(): Promise<void> }>
}
type DirectoryHandle = {
  name: string
  getFileHandle(name: string, options?: { create?: boolean }): Promise<FileHandle>
  removeEntry(name: string): Promise<void>
}
type PickerWindow = {
  showDirectoryPicker?: (options?: { mode?: 'read' | 'readwrite' }) => Promise<DirectoryHandle>
  isSecureContext?: boolean
}

/** 当前浏览器能否把文件写进使用者本机目录。 */
export function supportsDirectoryExport(): boolean {
  const scope = window as unknown as PickerWindow
  return typeof scope.showDirectoryPicker === 'function' && Boolean(scope.isSecureContext)
}

/** 用户取消选择时抛出，上层据此静默返回。 */
export class PickCancelled extends Error {}

/**
 * 逐条倍率 → 查询串 ``order_key|倍率`` 逗号分隔（与后端 `_parse_speeds` 对应）。
 *
 * 只发非 1.0 的行：绝大多数台词是原速，全发一遍会让 URL 长出好几倍，
 * 而漏传的行在后端本来就按 1.0 处理。`order_key` 自带冒号，所以用 `|` 分隔。
 */
export function encodeSpeeds(speeds: Record<string, number>): string {
  return Object.entries(speeds)
    .filter(([, value]) => Number.isFinite(value) && Math.abs(value - 1) > 1e-6)
    .map(([key, value]) => `${key}|${value}`)
    .join(',')
}

export type ExportProgress = { done: number; total: number; current: string }

/** 必须与后端 `studio/exports.py` 的 MANIFEST_APP 一致：这是"这份清单是我写的"的凭据。 */
const MANIFEST_APP = 'qwen-tts-voiceover-studio'

/**
 * 读历史导出目录里旧版本写下的 manifest.json。
 *
 * 现在导出不再生成这个文件，但它是唯一能证明"这些 mp3 是我上次写的"的凭据：
 * 用它清掉本轮不再导出的旧音频，再把清单本身删掉 —— 导出目录里只该有音频。
 * 目录是用户自己选的，里面可能已经有别人的 `manifest.json`；没有应用标记就一律
 * 当作不认识，既不清文件也不动它。宁可不清理，也不能按别人的清单删用户的音频。
 */
async function readPreviousManifest(dir: DirectoryHandle): Promise<{ files: string[]; ours: boolean }> {
  try {
    const handle = await dir.getFileHandle('manifest.json')
    const parsed: unknown = JSON.parse(await (await handle.getFile()).text())
    if (!Array.isArray(parsed)) return { files: [], ours: false }
    const entries = parsed.filter(
      (entry): entry is { app?: unknown; file?: unknown } => typeof entry === 'object' && entry !== null,
    )
    if (entries.length === 0 || entries[0].app !== MANIFEST_APP) return { files: [], ours: false }
    const files = entries
      .map((entry) => entry.file)
      .filter((name): name is string => typeof name === 'string')
    return { files, ours: true }
  } catch {
    return { files: [], ours: false }
  }
}

/**
 * 按后端清单把成品写入使用者选定的文件夹：先清掉上一轮遗留、本轮不再存在的 mp3，
 * 再逐条拉取音频落盘。目录里只有音频文件 —— 不写 manifest.json。
 *
 * ``speeds`` 只是转给后端：变速率发生在服务器给的那条 url 上（源文件不动），
 * 这里拿到的已经是该条自己的倍率，同一个文件夹里可以混着原速和加速的台词。
 */
export async function exportToDirectory(
  projectId: string,
  excerptLength: number,
  speeds: Record<string, number>,
  onProgress?: (progress: ExportProgress) => void,
): Promise<{ directory: string; count: number; failed: string[] }> {
  const scope = window as unknown as PickerWindow
  const picker = scope.showDirectoryPicker
  if (!picker) throw new Error('当前浏览器不支持直接写入本机文件夹')

  // 先弹目录框：showDirectoryPicker 必须在用户手势的有效期内调用，
  // 不能先 await 网络请求（超过浏览器的 transient activation 窗口就会被拒绝）。
  let dir: DirectoryHandle
  try {
    dir = await picker.call(scope, { mode: 'readwrite' })
  } catch (error) {
    if ((error as Error).name === 'AbortError') throw new PickCancelled('已取消选择文件夹')
    throw error
  }

  const query = new URLSearchParams({ excerpt_length: String(excerptLength) })
  const encoded = encodeSpeeds(speeds)
  if (encoded) query.set('speeds', encoded)
  const plan = await apiGet<ExportPlanResponse>(
    `/api/projects/${projectId}/outputs/export-plan?${query.toString()}`,
  )
  const files = plan.files
  if (files.length === 0) throw new Error('当前项目没有可导出的成品')

  const wanted = new Set(files.map((file) => file.file))
  const previous = await readPreviousManifest(dir)
  for (const stale of previous.files) {
    if (wanted.has(stale) || !stale.toLowerCase().endsWith('.mp3')) continue
    await dir.removeEntry(stale).catch(() => undefined)
  }
  if (previous.ours) await dir.removeEntry('manifest.json').catch(() => undefined)

  const failed: string[] = []
  let written = 0
  for (let index = 0; index < files.length; index += 1) {
    const file = files[index]
    onProgress?.({ done: index, total: files.length, current: file.file })
    const url = fileUrl(file.url)
    try {
      if (!url) throw new Error('缺少可访问的音频地址')
      const response = await fetch(url, { cache: 'no-store' })
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const handle = await dir.getFileHandle(file.file, { create: true })
      const writable = await handle.createWritable()
      await writable.write(await response.blob())
      await writable.close()
      written += 1
    } catch {
      failed.push(file.file)
    }
  }

  onProgress?.({ done: files.length, total: files.length, current: '' })
  return { directory: dir.name, count: written, failed }
}
