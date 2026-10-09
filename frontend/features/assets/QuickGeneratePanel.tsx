'use client'

import { useState } from 'react'
import { apiPost, fileUrl } from '@/lib/api'
import type { Notify, VoiceAsset } from '@/lib/types'
import { Button, Label, LanguageSelect, OutputBox, VoiceSelect } from '@/components/ui'

type QuickGeneratePanelProps = {
  projectId: string
  voices: VoiceAsset[]
  notify: Notify
}

/** 快速生成面板：选一个已归档声线，直接出一段 128kbps MP3。 */
export function QuickGeneratePanel({ projectId, voices, notify }: QuickGeneratePanelProps) {
  const [voiceId, setVoiceId] = useState('')
  const [language, setLanguage] = useState('Chinese')
  const [text, setText] = useState('')
  const [resultUrl, setResultUrl] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const generate = async () => {
    if (!voiceId) { notify('请选择声线'); return }
    if (!text.trim()) { notify('请填写文本'); return }
    setBusy(true)
    try {
      const result = await apiPost<{ output_path: string; url: string | null }>(`/api/projects/${projectId}/voices/${voiceId}/generate`, { text, language })
      setResultUrl(result.url); notify('已生成 128kbps MP3')
    } catch (error) { notify((error as Error).message) } finally { setBusy(false) }
  }

  return <>
    <div className="control-panel">
      <Label>声线</Label>
      <VoiceSelect voices={voices} value={voiceId} onChange={setVoiceId} placeholderAlways />
      <Label>语言</Label>
      <LanguageSelect value={language} onChange={setLanguage} />
      <Label>文本</Label>
      <textarea value={text} onChange={(event) => setText(event.target.value)} />
      <Button primary disabled={busy} onClick={generate}>{busy ? '生成中…' : '生成 128kbps MP3'}</Button>
    </div>
    <OutputBox label="♫ 输出" url={fileUrl(resultUrl)} downloadText="下载 MP3" />
  </>
}
