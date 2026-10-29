'use client'

import { useState } from 'react'
import { apiPost, fileUrl } from '@/lib/api'
import { applyPromptAssist } from '@/lib/promptAssist'
import { PROMPT_PLACEHOLDER } from '@/lib/promptPresets'
import type { Notify } from '@/lib/types'
import { Button, Label, LanguageSelect, OutputBox, PromptAssist, useConfirmDialog } from '@/components/ui'

const DEFAULT_TEXT = '请输入需要生成的文本。'
const DEFAULT_PROMPT = '清晰、自然、温和的成年女性普通话，语速适中。'

/** VoiceDesign 独立试听：自然语言描述直接出一段试听，不进入项目流程。 */
export function DesignTool({ notify }: { notify: Notify }) {
  const [language, setLanguage] = useState('Chinese')
  const [text, setText] = useState(DEFAULT_TEXT)
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT)
  const [busy, setBusy] = useState(false)
  const [resultUrl, setResultUrl] = useState<string | null>(null)

  const { confirm, dialogElement } = useConfirmDialog()
  // 提示词仍是初始默认值时不弹确认；配套文本只在文本框为默认值/空时填入。
  const handlePromptAssist = (nextPrompt: string, nextAudition: string) =>
    applyPromptAssist({
      confirm,
      currentPrompt: prompt,
      nextPrompt,
      nextAudition,
      untouchedPrompt: DEFAULT_PROMPT,
      currentAudition: text,
      untouchedAudition: (value) => !value.trim() || value.trim() === DEFAULT_TEXT,
      setPrompt,
      setAudition: setText,
    })

  const run = async () => {
    setBusy(true)
    try {
      if (!text.trim() || !prompt.trim()) { notify('文本和声线提示词均为必填项'); return }
      const result = await apiPost<{ url: string | null }>('/api/tools/design', { text, prompt, language })
      setResultUrl(result.url); notify('VoiceDesign 试听已生成')
    } catch (error) { notify((error as Error).message) } finally { setBusy(false) }
  }

  return <>
    <div className="control-panel">
      <Label>声线提示词</Label>
      <PromptAssist onApply={handlePromptAssist} />
      <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder={PROMPT_PLACEHOLDER} />
      <Label>语言</Label>
      <LanguageSelect value={language} onChange={setLanguage} />
      <Label>文本内容</Label>
      <textarea value={text} onChange={(event) => setText(event.target.value)} />
      <Button primary disabled={busy} onClick={run}>{busy ? '处理中…' : '开始生成'}</Button>
    </div>
    <OutputBox label="♫ 输出预览" url={fileUrl(resultUrl)} />
    {dialogElement}
  </>
}
