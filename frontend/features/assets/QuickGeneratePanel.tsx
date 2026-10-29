'use client'

import { useState } from 'react'
import { apiPost, fileUrl } from '@/lib/api'
import { LANGUAGE_LABELS } from '@/lib/constants'
import type { Notify, VoiceAsset } from '@/lib/types'
import { AudioPlayer, Button, LanguageSelect, VoiceSelect } from '@/components/ui'

type QuickGeneratePanelProps = {
  projectId: string
  voices: VoiceAsset[]
  notify: Notify
}

/** 快速试听：挑一条本项目清单里的声线、填一句话，当场出一段 128kbps MP3。
 *
 * 产物落在 {项目目录}/outputs/quick/，既不写库、也不进批量任务与成品清单——
 * 纯试听用，刷新页面即失去入口，所以卡片右上角常驻「不计入项目」标记。
 *
 * 单卡片纵向排布（声线+语言一行、文本一行、动作+输出一行），
 * 不再用左右分栏：原来右侧输出盒 min-height 215px，生成前大半是空气，
 * 和左侧表单高度对不齐，整块版面重心是歪的。
 *
 * 语言默认跟随所选声线（声线自带 language 字段），可手动改写；
 * 一旦手动改过就记 touched，之后换声线不再把用户的选择弹回去。
 */
export function QuickGeneratePanel({ projectId, voices, notify }: QuickGeneratePanelProps) {
  const [voiceId, setVoiceId] = useState('')
  const [language, setLanguage] = useState('Chinese')
  const [languageTouched, setLanguageTouched] = useState(false)
  const [text, setText] = useState('')
  const [resultUrl, setResultUrl] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const voiceLanguage = voices.find((item) => item.id === voiceId)?.language ?? ''
  // 声线素材语言与当前选择不一致：跨语种克隆技术上可行，但效果取决于原素材，不拦着、只提示。
  const mismatch = Boolean(voiceLanguage) && voiceLanguage !== language

  const pickVoice = (id: string) => {
    setVoiceId(id)
    const next = voices.find((item) => item.id === id)
    if (next?.language && !languageTouched) setLanguage(next.language)
    setResultUrl(null)
  }

  const pickLanguage = (value: string) => {
    setLanguage(value)
    setLanguageTouched(true)
  }

  const generate = async () => {
    if (!voiceId) { notify('请选择声线'); return }
    if (!text.trim()) { notify('请填写文本'); return }
    setBusy(true)
    try {
      const result = await apiPost<{ output_path: string; url: string | null }>(
        `/api/projects/${projectId}/voices/${voiceId}/generate`, { text, language })
      setResultUrl(result.url)
      notify('已生成 128kbps MP3（试听产物，不计入项目）')
    } catch (error) { notify((error as Error).message) } finally { setBusy(false) }
  }

  const output = fileUrl(resultUrl)

  return (
    <div className="quick-panel">
      <div className="quick-head">
        <h3>快速试听</h3>
        <span className="quick-badge">不计入项目</span>
        <span className="quick-head-hint">用本项目清单里的声线当场读一句，产物只供试听</span>
      </div>

      <div className="quick-grid">
        <div className="quick-field">
          <span>声线</span>
          <VoiceSelect
            voices={voices}
            value={voiceId}
            onChange={pickVoice}
            placeholder={voices.length ? '请选择声线' : '本项目清单还没有声线'}
            placeholderAlways
          />
        </div>
        <div className="quick-field">
          <span>语言</span>
          <LanguageSelect value={language} onChange={pickLanguage} />
        </div>
      </div>

      <div className="quick-field quick-text">
        <span>文本</span>
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="输入要试听的一句话，例如：欢迎使用声音工坊，这是一段试听。"
        />
      </div>

      {mismatch && (
        <p className="quick-warn">
          该声线由 {LANGUAGE_LABELS[voiceLanguage] ?? voiceLanguage} 素材创建，改用 {LANGUAGE_LABELS[language] ?? language} 生成可能不自然。
        </p>
      )}

      <div className="quick-foot">
        <Button primary disabled={busy} onClick={generate}>{busy ? '生成中…' : '生成 128kbps MP3'}</Button>
        {output
          ? <div className="quick-result">
              <span className="quick-result-label">输出</span>
              {/* wide：输出行要占满按钮右侧的余量（CSS 里再限宽到 520px），窄形态挤在一角会显得空 */}
              <AudioPlayer url={output} wide />
              <Button href={output} download>下载 MP3</Button>
            </div>
          : <span className="quick-idle">生成后在这一行试听与下载</span>}
      </div>
    </div>
  )
}
