'use client'

import { useRef, useState } from 'react'
import { apiUpload, fileUrl } from '@/lib/api'
import { AUDIO_ACCEPT } from '@/lib/constants'
import type { Notify } from '@/lib/types'
import { Button, Checkbox, Label, LanguageSelect, OutputBox } from '@/components/ui'

/** VoiceClone 独立克隆：上传参考音频直接克隆试听，不保存资产。 */
export function CloneTool({ notify }: { notify: Notify }) {
  const [language, setLanguage] = useState('Chinese')
  const [text, setText] = useState('请输入需要生成的文本。')
  const [refText, setRefText] = useState('')
  const [xVectorOnly, setXVectorOnly] = useState(false)
  const [busy, setBusy] = useState(false)
  const [resultUrl, setResultUrl] = useState<string | null>(null)
  const [fileLabel, setFileLabel] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  const run = async () => {
    setBusy(true)
    try {
      const file = fileRef.current?.files?.[0]
      if (!file) { notify('请上传参考音频'); return }
      const form = new FormData()
      form.append('file', file)
      form.append('text', text)
      form.append('language', language)
      form.append('ref_text', refText)
      form.append('x_vector_only', xVectorOnly ? 'true' : 'false')
      const result = await apiUpload<{ url: string | null }>('/api/tools/clone', form)
      setResultUrl(result.url); notify('克隆试听已生成')
    } catch (error) { notify((error as Error).message) } finally { setBusy(false) }
  }

  return <>
    <div className="control-panel">
      <Label>参考音频（WAV/MP3）</Label>
      <input type="file" accept={AUDIO_ACCEPT} ref={fileRef}
        onChange={(event) => setFileLabel(Array.from(event.target.files ?? []).map((file) => file.name).join(', '))} />
      {fileLabel && <p className="hint">已选择：{fileLabel}</p>}
      <Label>参考音频对应文字</Label>
      <textarea value={refText} onChange={(event) => setRefText(event.target.value)} />
      <Checkbox checked={xVectorOnly} onChange={setXVectorOnly}>
        仅声学模式（无需参考文字，但质量可能降低）
      </Checkbox>
      <Label>语言</Label>
      <LanguageSelect value={language} onChange={setLanguage} />
      <Label>文本内容</Label>
      <textarea value={text} onChange={(event) => setText(event.target.value)} />
      <Button primary disabled={busy} onClick={run}>{busy ? '处理中…' : '开始生成'}</Button>
    </div>
    <OutputBox label="♫ 输出预览" url={fileUrl(resultUrl)} />
  </>
}
