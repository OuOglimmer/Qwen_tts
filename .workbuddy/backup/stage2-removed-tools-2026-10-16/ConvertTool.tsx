'use client'

import { useRef, useState } from 'react'
import { apiUpload, fileUrl } from '@/lib/api'
import { AUDIO_ACCEPT } from '@/lib/constants'
import type { Notify } from '@/lib/types'
import { AudioPlayer, Button, Label } from '@/components/ui'

/** 音频转换：在 WAV（24kHz 单声道）与 128kbps MP3 之间互相转换，支持批量。 */
export function ConvertTool({ notify }: { notify: Notify }) {
  const [target, setTarget] = useState('MP3 (128kbps)')
  const [busy, setBusy] = useState(false)
  const [converted, setConverted] = useState<Array<{ name: string; url: string | null }>>([])
  const [fileLabel, setFileLabel] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  const run = async () => {
    setBusy(true)
    try {
      const files = fileRef.current?.files
      if (!files || files.length === 0) { notify('请选择要转换的音频文件'); return }
      const form = new FormData()
      for (const file of Array.from(files)) form.append('files', file)
      form.append('target', target)
      const result = await apiUpload<{ files: Array<{ name: string; url: string | null }> }>('/api/tools/convert', form)
      setConverted(result.files); notify(`已转换 ${result.files.length} 个文件`)
    } catch (error) { notify((error as Error).message) } finally { setBusy(false) }
  }

  return <>
    <div className="control-panel">
      <Label>选择音频（可多选）</Label>
      <input type="file" accept={AUDIO_ACCEPT} multiple ref={fileRef}
        onChange={(event) => setFileLabel(Array.from(event.target.files ?? []).map((file) => file.name).join(', '))} />
      {fileLabel && <p className="hint">已选择：{fileLabel}</p>}
      <Label>目标格式</Label>
      <select value={target} onChange={(event) => setTarget(event.target.value)}>
        <option value="MP3 (128kbps)">MP3 (128kbps)</option>
        <option value="WAV">WAV (24kHz)</option>
      </select>
      <Button primary disabled={busy} onClick={run}>{busy ? '处理中…' : '开始转换'}</Button>
    </div>
    <div className="download-box">
      <Label>♫ 输出预览</Label>
      {/* 多文件结果必须挂显式类名：裸 div 会继承 globals 给 .download-box>div 的
          grid + place-items:center，整块被收缩成一条窄条，里面的 wide 播放器跟着塌成 245px。 */}
      {converted.length === 0
        ? <div className="output-empty">转换结果会显示在这里</div>
        : <div className="convert-results">
          {converted.map((file) => <div className="convert-result" key={file.name}>
            <p className="hint">{file.name}</p>
            <AudioPlayer url={fileUrl(file.url)} wide />
            <a className="btn" href={fileUrl(file.url) ?? '#'} download>下载</a>
          </div>)}
        </div>}
    </div>
  </>
}
