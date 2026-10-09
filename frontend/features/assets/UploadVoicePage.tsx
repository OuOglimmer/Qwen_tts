'use client'

import { useRef, useState } from 'react'
import { apiUpload, fileUrl } from '@/lib/api'
import { AUDIO_ACCEPT } from '@/lib/constants'
import type { Notify } from '@/lib/types'
import { Button, Checkbox, Label, LanguageSelect, OutputBox, SectionTitle } from '@/components/ui'

/** 「上传声线」页：上传参考音频克隆试听并固化为可复用资产。 */
export function UploadVoicePage({ projectId, notify }: { projectId: string; notify: Notify }) {
  const [name, setName] = useState('')
  const [refText, setRefText] = useState('')
  const [xVectorOnly, setXVectorOnly] = useState(false)
  const [language, setLanguage] = useState('Chinese')
  const [previewText, setPreviewText] = useState('你好，这是已有声线的克隆试听。')
  const [tags, setTags] = useState('')
  const [notes, setNotes] = useState('')
  const [fileName, setFileName] = useState('')
  const [busy, setBusy] = useState(false)
  const [resultUrl, setResultUrl] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const submit = async () => {
    const file = fileRef.current?.files?.[0]
    if (!file) { notify('请选择参考音频文件（WAV/MP3）'); return }
    if (!name.trim()) { notify('请填写声线名称'); return }
    if (!previewText.trim()) { notify('请填写克隆试听文本'); return }
    setBusy(true)
    try {
      const form = new FormData()
      form.append('file', file)
      form.append('name', name)
      form.append('ref_text', refText)
      form.append('language', language)
      form.append('x_vector_only', xVectorOnly ? 'true' : 'false')
      form.append('preview_text', previewText)
      form.append('tags', tags)
      form.append('notes', notes)
      const result = await apiUpload<{ voice_id: string; preview_url: string | null }>(`/api/projects/${projectId}/voices/upload`, form)
      setResultUrl(result.preview_url)
      notify('克隆试听完成，声线已固化进资产库')
    } catch (error) { notify((error as Error).message) } finally { setBusy(false) }
  }

  return <>
    <SectionTitle title="上传声线" description="上传已有 WAV 或 MP3，生成一段克隆试听并固化为可复用资产，成功后自动进入声线资产库和「绑定声线」列表。" />
    <div className="panel-grid upload-layout">
      <div className="control-panel">
        <Label>声线名称</Label>
        <input value={name} onChange={(event) => setName(event.target.value)} />
        <Label>参考音频（WAV/MP3/FLAC，≤80MB）</Label>
        <input type="file" accept={AUDIO_ACCEPT} ref={fileRef} onChange={(event) => setFileName(event.target.files?.[0]?.name ?? '')} />
        {fileName && <p className="hint">已选择：{fileName}</p>}
        <Label>参考音频对应文字（高质量克隆必填）</Label>
        <textarea value={refText} onChange={(event) => setRefText(event.target.value)} />
        <Checkbox checked={xVectorOnly} onChange={setXVectorOnly}>
          仅声学模式（无需参考文字，但质量可能降低）
        </Checkbox>
        <Label>语言</Label>
        <LanguageSelect value={language} onChange={setLanguage} />
        <Label>克隆试听文本</Label>
        <textarea value={previewText} onChange={(event) => setPreviewText(event.target.value)} />
        <Label>标签</Label>
        <input value={tags} onChange={(event) => setTags(event.target.value)} placeholder="例如：女声,旁白,温柔" />
        <Label>备注</Label>
        <textarea value={notes} onChange={(event) => setNotes(event.target.value)} />
        <Button primary disabled={busy} onClick={submit}>{busy ? '模型克隆中，约需数十秒…' : '试听、固化并归档'}</Button>
      </div>
      <OutputBox label="♫ 克隆试听" url={fileUrl(resultUrl)} downloadText="下载试听" />
    </div>
  </>
}
