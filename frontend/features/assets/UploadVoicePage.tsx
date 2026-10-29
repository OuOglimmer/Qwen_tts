'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { apiGet, apiUpload, fileUrl } from '@/lib/api'
import { AUDIO_ACCEPT } from '@/lib/constants'
import type { Notify, VoiceAsset } from '@/lib/types'
import { Button, Checkbox, Label, LanguageSelect, OutputBox, SectionTitle, VoiceRegisterDialog, type VoiceDraft } from '@/components/ui'
import { ConvertPanel } from './ConvertPanel'

type Mode = 'clone' | 'convert'

/** 「上传声线」页：一页两模式。
 *
 *  模式一「上传克隆」把原来的「上传声线」与工具页 VoiceClone 合并成一条流程——
 *  两者的差别原本只有"存不存档"，现在由两个独立按钮表达：
 *    · 试听 → /api/tools/clone，产物落临时目录，不写库；
 *    · 入库 → 弹出「登记声线」命名与打标签，再走 /api/voices/upload 固化。
 *  表单里不再出现名称/标签，命名只发生在点「入库」之后。
 *
 *  模式二「音频转化」= 原工具页音频转换迁入，补上了逐文件进度与 ZIP 批量下载。
 *  两个面板都保持挂载（用 class 切换显示），切来切去不会丢掉已选的文件。 */
export function UploadVoicePage({ notify }: { notify: Notify }) {
  const [mode, setMode] = useState<Mode>('clone')

  const [refText, setRefText] = useState('')
  const [refLanguage, setRefLanguage] = useState('Chinese')
  const [xVectorOnly, setXVectorOnly] = useState(false)
  const [language, setLanguage] = useState('Chinese')
  const [previewText, setPreviewText] = useState('你好，这是已有声线的克隆试听。')
  const [fileName, setFileName] = useState('')
  const [tagPool, setTagPool] = useState<string[]>([])

  const [auditionBusy, setAuditionBusy] = useState(false)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [archiveBusy, setArchiveBusy] = useState(false)
  const [resultUrl, setResultUrl] = useState<string | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  // 已有标签供登记弹窗一键复用，避免同义标签各写一遍。拉不到就不给建议，不影响入库。
  useEffect(() => {
    apiGet<VoiceAsset[]>('/api/library/voices')
      .then((rows) => setTagPool(Array.from(new Set(
        rows.flatMap((voice) => (voice.tags || '').split(',').map((tag) => tag.trim())).filter(Boolean),
      )).sort()))
      .catch(() => undefined)
  }, [])

  const currentFile = () => fileRef.current?.files?.[0] ?? null

  /** 两个按钮共用的前置校验：缺什么就直说缺什么，不要让用户点下去才在后端报错。 */
  const validate = (): File | null => {
    const file = currentFile()
    if (!file) { notify('请选择参考音频文件（WAV/MP3/FLAC）'); return null }
    if (!previewText.trim()) { notify('请填写试听文本'); return null }
    if (!xVectorOnly && !refText.trim()) { notify('高质量克隆需要「参考音频对应文字」，或勾选「仅声学模式」'); return null }
    return file
  }

  /** 试听与入库共用的表单。
   *  注意朗读文本的字段名：`/api/voices/upload` 叫 `preview_text`，`/api/tools/clone`
   *  历史上叫 `text`。这里按前者的叫法发一份，clone 那边两种都收（见 tool_clone），
   *  别只发一边——曾经就因为字段名不一致，点「试听」直接 422。 */
  const buildForm = (file: File) => {
    const form = new FormData()
    form.append('file', file)
    form.append('ref_text', refText)
    form.append('language', language)
    form.append('x_vector_only', xVectorOnly ? 'true' : 'false')
    form.append('preview_text', previewText)
    return form
  }

  const audition = async () => {
    const file = validate()
    if (!file) return
    setAuditionBusy(true)
    try {
      const result = await apiUpload<{ url: string | null }>('/api/tools/clone', buildForm(file))
      setResultUrl(result.url)
      notify('试听已生成（未入库，刷新即失效）')
    } catch (error) { notify((error as Error).message) } finally { setAuditionBusy(false) }
  }

  const register = async (draft: VoiceDraft) => {
    const file = validate()
    if (!file) { setDialogOpen(false); return }
    setArchiveBusy(true)
    try {
      const form = buildForm(file)
      form.append('name', draft.name)
      form.append('ref_language', refLanguage)
      form.append('tags', draft.tags)
      form.append('notes', draft.notes)
      const result = await apiUpload<{ voice_id: string; preview_url: string | null }>('/api/voices/upload', form)
      setResultUrl(result.preview_url)
      setDialogOpen(false)
      notify(`「${draft.name}」已入库到公共声线库`)
    } catch (error) { notify((error as Error).message) } finally { setArchiveBusy(false) }
  }

  const summary = useMemo(() => {
    const parts = [fileName || '未选择文件', `试听语种 ${language}`]
    if (refLanguage) parts.push(`素材语种 ${refLanguage}`)
    parts.push(xVectorOnly ? '仅声学模式' : `参考文字 ${refText.trim().length} 字`)
    return parts.join(' · ')
  }, [fileName, language, refLanguage, xVectorOnly, refText])

  return <>
    <SectionTitle title="上传声线" description="上传参考音频做克隆试听，或者把已有音频在 WAV 与 MP3 之间转换。试听产物刷新即失效；只有点「入库」并登记名称与标签，声线才会进入全局声线库。" />

    <div className="mode-switch" role="tablist">
      <button type="button" role="tab" aria-selected={mode === 'clone'}
        className={mode === 'clone' ? 'mode-tab active' : 'mode-tab'} onClick={() => setMode('clone')}>
        上传克隆
      </button>
      <button type="button" role="tab" aria-selected={mode === 'convert'}
        className={mode === 'convert' ? 'mode-tab active' : 'mode-tab'} onClick={() => setMode('convert')}>
        音频转化
      </button>
    </div>

    <div className={mode === 'clone' ? 'mode-panel' : 'mode-panel is-hidden'}>
      <div className="panel-grid upload-layout">
        <div className="control-panel">
          <Label>参考音频（WAV/MP3/FLAC，≤80MB）</Label>
          <input type="file" accept={AUDIO_ACCEPT} ref={fileRef}
            onChange={(event) => setFileName(event.target.files?.[0]?.name ?? '')} />
          {fileName && <p className="hint">已选择：{fileName}</p>}

          <Label>参考音频语种（这条素材本身是什么语言）</Label>
          <LanguageSelect value={refLanguage} onChange={setRefLanguage} />

          <Label>参考音频对应文字（高质量克隆必填）</Label>
          <textarea value={refText} onChange={(event) => setRefText(event.target.value)} />
          <Checkbox checked={xVectorOnly} onChange={setXVectorOnly}>
            仅声学模式（无需参考文字，但质量可能降低）
          </Checkbox>

          <div className="form-divider" />

          <Label>试听文本语种（这一段用什么语言朗读）</Label>
          <LanguageSelect value={language} onChange={setLanguage} />

          <Label>试听文本</Label>
          <textarea value={previewText} onChange={(event) => setPreviewText(event.target.value)} />

          <div className="button-row publish-row">
            <Button disabled={auditionBusy || archiveBusy} onClick={audition}>
              {auditionBusy ? '生成中…' : '试听'}
            </Button>
            <Button primary disabled={auditionBusy || archiveBusy} onClick={() => setDialogOpen(true)}>
              入库
            </Button>
          </div>
          <p className="hint">两个按钮互不依赖：只想听一下点「试听」（不留档）；要留存点「入库」。</p>
        </div>
        <OutputBox label="♫ 试听结果" url={fileUrl(resultUrl)} downloadText="下载试听" />
      </div>
    </div>

    <div className={mode === 'convert' ? 'mode-panel' : 'mode-panel is-hidden'}>
      <ConvertPanel notify={notify} />
    </div>

    <VoiceRegisterDialog
      open={dialogOpen}
      busy={archiveBusy}
      summary={summary}
      suggestions={tagPool}
      onCancel={() => setDialogOpen(false)}
      onSubmit={register}
    />
  </>
}
