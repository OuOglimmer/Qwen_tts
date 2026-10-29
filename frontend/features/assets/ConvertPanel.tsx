'use client'

import { useRef, useState } from 'react'
import { apiPostBlob, apiUpload, fileUrl, saveBlob } from '@/lib/api'
import { AUDIO_ACCEPT } from '@/lib/constants'
import type { Notify } from '@/lib/types'
import { Button, Label } from '@/components/ui'

/** 目标格式。value 必须与后端 tools.convert_files 的判断串逐字一致：
 *  'MP3 (128kbps)' 走编码分支，其余一律按 WAV 重采样。 */
const TARGETS = [
  { value: 'MP3 (128kbps)', title: 'MP3 · 128kbps', note: '适合分发 · 体积小' },
  { value: 'WAV', title: 'WAV · 24kHz 单声道', note: '项目内使用 · 无损' },
] as const

type Status = 'wait' | 'running' | 'done' | 'failed'

type ConvertRow = {
  key: number
  file: File
  status: Status
  url?: string | null
  /** 打包下载用的相对路径 <conversion_id>/<文件名>，由后端在响应里给出。 */
  item?: string
  error?: string
}

const STATE_TEXT: Record<Status, string> = {
  wait: '等待中',
  running: '转换中…',
  done: '已完成',
  failed: '失败',
}

function formatSize(bytes: number): string {
  return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`
}

function sourceFormat(filename: string): string {
  const suffix = filename.split('.').pop() ?? ''
  return suffix && suffix !== filename ? suffix.toUpperCase() : '未知'
}

/** 音频转化：WAV(24kHz 单声道) ↔ MP3(128kbps)，可多选。
 *
 *  逐文件顺序发起请求（而不是一次提交全部）：这样才有真实的逐条进度，
 *  且单个文件失败不会拖垮整批——后端 convert_files 是遇错即抛，整批提交等于全有或全无。
 *  格式转换是纯 CPU（重采样 + 编码），不碰 GPU，顺序执行不会与批量配音抢卡。 */
export function ConvertPanel({ notify }: { notify: Notify }) {
  const [target, setTarget] = useState<string>(TARGETS[0].value)
  const [rows, setRows] = useState<ConvertRow[]>([])
  const [busy, setBusy] = useState(false)
  const [zipping, setZipping] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)
  const seqRef = useRef(0)

  const addFiles = (picked: File[]) => {
    if (picked.length === 0) return
    setRows((prev) => {
      const seen = new Set(prev.map((row) => `${row.file.name}|${row.file.size}|${row.file.lastModified}`))
      const added = picked
        .filter((file) => !seen.has(`${file.name}|${file.size}|${file.lastModified}`))
        .map((file) => ({ key: seqRef.current++, file, status: 'wait' as Status }))
      return [...prev, ...added]
    })
    // 清空 input：否则再次选中同一批文件不会触发 change（同名同大小的文件被浏览器视为没变）。
    if (fileRef.current) fileRef.current.value = ''
  }

  const patch = (key: number, next: Partial<ConvertRow>) =>
    setRows((prev) => prev.map((row) => (row.key === key ? { ...row, ...next } : row)))

  const remove = (key: number) => setRows((prev) => prev.filter((row) => row.key !== key))

  const run = async () => {
    const pending = rows.filter((row) => row.status === 'wait' || row.status === 'failed')
    if (pending.length === 0) { notify(rows.length === 0 ? '请先选择要转换的音频文件' : '没有待转换的文件'); return }
    setBusy(true)
    let ok = 0
    let failed = 0
    for (const row of pending) {
      patch(row.key, { status: 'running', error: undefined })
      try {
        const form = new FormData()
        form.append('files', row.file)
        form.append('target', target)
        const result = await apiUpload<{ conversion_id: string; files: Array<{ name: string; url: string | null }> }>(
          '/api/tools/convert', form,
        )
        const first = result.files[0]
        patch(row.key, {
          status: 'done',
          url: first?.url ?? null,
          item: first ? `${result.conversion_id}/${first.name}` : undefined,
        })
        ok += 1
      } catch (error) {
        patch(row.key, { status: 'failed', error: (error as Error).message })
        failed += 1
      }
    }
    setBusy(false)
    notify(failed === 0 ? `已转换 ${ok} 个文件` : `转换完成：成功 ${ok} 个，失败 ${failed} 个`)
  }

  const doneItems = rows.filter((row) => row.status === 'done' && row.item).map((row) => row.item as string)
  const doneCount = rows.filter((row) => row.status === 'done').length
  const running = rows.some((row) => row.status === 'running')

  const downloadZip = async () => {
    if (doneItems.length === 0) { notify('还没有转换完成的文件'); return }
    setZipping(true)
    try {
      const blob = await apiPostBlob('/api/tools/conversions/archive', { items: doneItems })
      saveBlob(blob, '音频转换结果.zip')
      notify(`已打包 ${doneItems.length} 个文件`)
    } catch (error) { notify((error as Error).message) } finally { setZipping(false) }
  }

  return <div className="convert-card">
    <div className="convert-head">
      <div>
        <p className="convert-title">待转文件</p>
        <p className="hint">{rows.length === 0 ? '还没有选择文件' : `共 ${rows.length} 个 · 已完成 ${doneCount} 个`}</p>
      </div>
      <Button primary disabled={zipping || busy || doneItems.length === 0} onClick={downloadZip}>
        {zipping ? '打包中…' : `全部下载（ZIP）${doneItems.length > 0 ? ` · ${doneItems.length}` : ''}`}
      </Button>
    </div>

    {rows.length > 0 && <>
      <div className="convert-bar"><span style={{ width: `${Math.round((doneCount / rows.length) * 100)}%` }} /></div>
      <p className="hint convert-bar-note">{doneCount} / {rows.length} 完成{running ? ' · 转换中' : ''}</p>
    </>}

    <label className="convert-picker">
      <input type="file" accept={AUDIO_ACCEPT} multiple ref={fileRef}
        onChange={(event) => addFiles(Array.from(event.target.files ?? []))} />
      <span>点击选择音频文件（可多选）</span>
      <small>{AUDIO_ACCEPT.split(',').join(' ')}</small>
    </label>

    {rows.length > 0 && <ul className="convert-list">
      <li className="convert-item convert-item-head" aria-hidden="true">
        <span>文件名</span><span>大小</span><span>源格式</span><span>状态</span><span>操作</span>
      </li>
      {rows.map((row) => <li className={`convert-item is-${row.status}`} key={row.key}>
        <span className="convert-name" title={row.file.name}>{row.file.name}</span>
        <span className="convert-meta">{formatSize(row.file.size)}</span>
        <span className="convert-meta">{sourceFormat(row.file.name)}</span>
        <span className={`convert-state is-${row.status}`}>
          {row.error ? <span title={row.error}>{STATE_TEXT[row.status]}</span> : STATE_TEXT[row.status]}
        </span>
        <span className="convert-op">
          {row.status === 'done' && <a className="btn mini-btn" href={fileUrl(row.url) ?? '#'} download>下载</a>}
          {row.error && <span className="convert-error" title={row.error}>{row.error}</span>}
          <Button small disabled={busy} onClick={() => remove(row.key)}>移除</Button>
        </span>
      </li>)}
    </ul>}

    <div className="convert-targets">
      <Label>目标格式</Label>
      <div className="convert-target-row">
        {TARGETS.map((item) => <button
          type="button"
          key={item.value}
          className={target === item.value ? 'convert-target selected' : 'convert-target'}
          disabled={busy}
          onClick={() => setTarget(item.value)}
        >
          <strong>{item.title}</strong>
          <span>{item.note}</span>
        </button>)}
      </div>
    </div>

    <div className="convert-actions">
      <Button primary disabled={busy || rows.length === 0} onClick={run}>{busy ? '转换中…' : '开始转换'}</Button>
      <Button disabled={busy || rows.length === 0} onClick={() => setRows([])}>清空列表</Button>
    </div>

    <p className="hint convert-warn">产物在临时目录，<strong>刷新页面即失效</strong>，请及时下载。不进声线库、不进成品、不进导出包。</p>
  </div>
}
