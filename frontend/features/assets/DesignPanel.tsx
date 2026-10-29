'use client'

import { useEffect, useMemo, useState } from 'react'
import { apiGet, apiPost, fileUrl } from '@/lib/api'
import { applyPromptAssist } from '@/lib/promptAssist'
import { PROMPT_PLACEHOLDER } from '@/lib/promptPresets'
import type { Notify, VoiceAsset } from '@/lib/types'
import {
  AudioPlayer, Button, LanguageSelect, PromptAssist, VoiceRegisterDialog, useConfirmDialog, type VoiceDraft,
} from '@/components/ui'

const DEFAULT_TEXT = '请输入需要生成的文本。'
const DEFAULT_PROMPT = '清晰、自然、温和的成年女性普通话，语速适中。'

/** 「文字描述生成」：不依赖任何项目，用自然语言造一把新声线。
 *
 *  与「快速试听」是一对互补的工具：这里**造声线**（描述 → 音色），
 *  那里**用声线**（音色 + 文字 → 音频）。两者共用同一张卡片骨架。
 *
 *  两个按钮对应两种意图，和「上传声线」页保持一致：
 *    · 试听 → /api/tools/design，产物落临时目录，不写库；
 *    · 入库 → 复用同一个「登记声线」弹窗（名称 + 标签 + 备注，project_id 一律空），
 *      确认后走 /api/voices/design-archive 固化成公共声线。
 *
 *  关键细节：入库用的是**用户刚试听的那一段**（design_id 由后端回传）。
 *  重跑一次会换一个随机 seed，试听的和入库的就不是同一把声音了；所以只要
 *  描述/语言/文本没变，就直接复用上一次的产物，不变才重新生成。
 */
export function DesignPanel({ notify, onArchived }: { notify: Notify; onArchived: () => void }) {
  const [prompt, setPrompt] = useState(DEFAULT_PROMPT)
  const [language, setLanguage] = useState('Chinese')
  const [text, setText] = useState(DEFAULT_TEXT)
  const [take, setTake] = useState<{ designId: string; url: string; key: string } | null>(null)
  const [pending, setPending] = useState<{ designId: string; prompt: string; language: string; text: string } | null>(null)
  const [busy, setBusy] = useState(false)
  const [archiving, setArchiving] = useState(false)
  const [tagPool, setTagPool] = useState<string[]>([])

  const { confirm, dialogElement } = useConfirmDialog()

  // 已有标签供弹窗一键复用，避免同义标签各写一遍；拉不到就不给建议，不影响入库。
  useEffect(() => {
    apiGet<VoiceAsset[]>('/api/library/voices')
      .then((rows) => setTagPool(Array.from(new Set(
        rows.flatMap((voice) => (voice.tags || '').split(',').map((tag) => tag.trim())).filter(Boolean),
      )).sort()))
      .catch(() => undefined)
  }, [])

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

  const signature = useMemo(() => `${prompt.trim()}||${language}||${text.trim()}`, [prompt, language, text])
  const output = take ? fileUrl(take.url) : null

  const validate = () => {
    if (!prompt.trim()) { notify('请填写声线提示词'); return false }
    if (!text.trim()) { notify('请填写试听文本'); return false }
    return true
  }

  /** 拿到"要固化的那一段"：输入没变就复用刚试听的结果，变了才真去生成。 */
  const ensureTake = async () => {
    if (take && take.key === signature) return take
    const result = await apiPost<{ design_id: string; url: string | null }>(
      '/api/tools/design', { prompt, text, language })
    const next = { designId: result.design_id, url: result.url ?? '', key: signature }
    setTake(next)
    return next
  }

  const audition = async () => {
    if (!validate()) return
    setBusy(true)
    try {
      await ensureTake()
      notify('试听已生成；要留存请点「入库」')
    } catch (error) { notify((error as Error).message) } finally { setBusy(false) }
  }

  const openRegister = async () => {
    if (!validate()) return
    setBusy(true)
    try {
      const current = await ensureTake()
      setPending({ designId: current.designId, prompt: prompt.trim(), language, text: text.trim() })
    } catch (error) { notify((error as Error).message) } finally { setBusy(false) }
  }

  const register = async (draft: VoiceDraft) => {
    if (!pending) return
    setArchiving(true)
    try {
      await apiPost<{ voice_id: string }>('/api/voices/design-archive', {
        design_id: pending.designId,
        name: draft.name,
        prompt: pending.prompt,
        text: pending.text,
        language: pending.language,
        tags: draft.tags,
        notes: draft.notes,
      })
      setPending(null)
      onArchived()
      notify(`「${draft.name}」已入库到公共声线库`)
    } catch (error) { notify((error as Error).message) } finally { setArchiving(false) }
  }

  const summary = `${prompt.trim().slice(0, 28)}${prompt.trim().length > 28 ? '…' : ''} · 试听语种 ${language}`

  return (
    <div className="design-panel">
      <div className="design-head">
        <h3>文字描述生成</h3>
        <span className="design-badge">不写项目</span>
        <span className="design-head-hint">用一段自然语言描述造一把新声线；「试听」只出一段，「入库」才会固化进声线库</span>
      </div>

      <div className="design-grid">
        <div className="design-field">
          <span>语言</span>
          <LanguageSelect value={language} onChange={setLanguage} />
        </div>
        <div className="design-field">
          <span>提示词模板</span>
          <PromptAssist onApply={handlePromptAssist} />
        </div>
      </div>

      <div className="design-field design-text">
        <span>声线提示词</span>
        <textarea
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          placeholder={PROMPT_PLACEHOLDER}
        />
      </div>

      <div className="design-field design-text">
        <span>试听文本</span>
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="这段文字只用来试听；入库后声线可读任意文本。"
        />
      </div>

      <p className="design-note">
        未试听直接点「入库」会先按当前描述生成一段；已试听则直接固化你刚听到的那一段（描述、语言或文本改动过才会重新生成）。
      </p>

      <div className="design-foot">
        <div className="design-actions">
          <Button disabled={busy || archiving} onClick={audition}>{busy ? '生成中…' : '试听'}</Button>
          <Button primary disabled={busy || archiving} onClick={openRegister}>入库</Button>
        </div>
        {output
          ? <div className="design-result">
              <span className="design-result-label">输出</span>
              {/* wide：输出行占满按钮右侧余量（CSS 里再限宽到 520px），否则窄形态挤在一角显得空 */}
              <AudioPlayer url={output} wide />
              <Button href={output} download>下载 MP3</Button>
            </div>
          : <span className="design-idle">生成后在这一行试听与下载</span>}
      </div>

      <VoiceRegisterDialog
        open={Boolean(pending)}
        busy={archiving}
        summary={summary}
        suggestions={tagPool}
        onCancel={() => setPending(null)}
        onSubmit={register}
      />
      {dialogElement}
    </div>
  )
}
