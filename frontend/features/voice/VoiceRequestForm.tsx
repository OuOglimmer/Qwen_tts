'use client'

import type { VoiceGenTask, VoiceRequest } from '@/lib/types'
import { applyPromptAssist } from '@/lib/promptAssist'
import { Button, Label, LanguageSelect, PromptAssist, useConfirmDialog } from '@/components/ui'
import { PROMPT_PLACEHOLDER } from '@/lib/promptPresets'
import type { VoiceDraftState } from './useVoiceDraft'

type VoiceRequestFormProps = {
  requests: VoiceRequest[]
  activeRequestId: string | null
  activeRequest: VoiceRequest | null
  draft: VoiceDraftState
  genRunning: boolean
  genTask: VoiceGenTask
  progress: Record<string, number> | null
  onSelectSlot: (requestId: string | null) => void
  onBulkAdd: () => void
  onGenerate: () => void
  onCancelGenerate: () => void
  onSaveDraft: () => void
  onDeleteSlot: () => void
}

/** 左侧控制面板：槽位选择、批量建槽、人物设定表单、生成/草稿操作与进度。 */
export function VoiceRequestForm({ requests, activeRequestId, activeRequest, draft, genRunning, genTask, progress, onSelectSlot, onBulkAdd, onGenerate, onCancelGenerate, onSaveDraft, onDeleteSlot }: VoiceRequestFormProps) {
  const { confirm, dialogElement } = useConfirmDialog()
  // 模板/组装结果覆盖已有提示词前需应用内确认；配套试听文本只在为空时填入，不覆盖已写内容。
  const handlePromptAssist = (nextPrompt: string, nextAudition: string) =>
    applyPromptAssist({
      confirm,
      currentPrompt: draft.prompt,
      nextPrompt,
      nextAudition,
      currentAudition: draft.auditionText,
      setPrompt: draft.setPrompt,
      setAudition: draft.setAuditionText,
    })
  return <div className="control-panel">
    <div className="slot-label-row">
      <Label>声线槽位</Label>
      <Button small onClick={onBulkAdd}>＋ 添加多条声线</Button>
    </div>
    <select value={activeRequestId ?? ''} onChange={(event) => onSelectSlot(event.target.value || null)}>
      <option value="">＋ 新声线（生成时自动创建）</option>
      {requests.map((request) => <option key={request.id} value={request.id}>{String(request.position).padStart(2, '0')} · {request.name} · {request.status}</option>)}
    </select>
    <Label>声线名称</Label>
    <input value={draft.name} onChange={(event) => draft.setName(event.target.value)} placeholder="例如：温柔旁白女生" />
    <Label>声线提示词</Label>
    <PromptAssist onApply={handlePromptAssist} />
    <textarea value={draft.prompt} onChange={(event) => draft.setPrompt(event.target.value)} placeholder={PROMPT_PLACEHOLDER} />
    <Label>试听文本</Label>
    <textarea value={draft.auditionText} onChange={(event) => draft.setAuditionText(event.target.value)} placeholder="用于试听的一句话" />
    <div className="control-row">
      <div><Label>语言</Label>
        <LanguageSelect value={draft.language} onChange={draft.setLanguage} />
      </div>
      <div><Label>本轮候选数</Label>
        <input type="number" min={1} max={4} value={draft.count} onChange={(event) => draft.setCount(Number(event.target.value))} />
      </div>
    </div>
    <div className="button-row">
      <Button primary disabled={genRunning} onClick={onGenerate}>{genRunning ? '候选生成中…' : '生成候选'}</Button>
      {genRunning
        ? <Button onClick={onCancelGenerate}>取消生成</Button>
        : <Button disabled={!activeRequestId} onClick={onSaveDraft}>保存草稿</Button>}
    </div>
    {genTask && !genTask.finished && (() => {
      const processed = genTask.generated + genTask.failed
      const pct = genTask.total ? Math.round((processed / genTask.total) * 100) : 0
      return <div className="gen-progress">
        <div className="progress-track"><div className="progress-fill" style={{ width: `${pct}%` }} /></div>
        <span className="hint">正在生成「{genTask.name}」的候选：{processed}/{genTask.total}（{pct}%）</span>
      </div>
    })()}
    {activeRequest && <>
      <Label>删除当前槽位</Label>
      <div className="button-row">
        <Button onClick={onDeleteSlot}>删除槽位</Button>
      </div>
    </>}
    {progress && <p className="hint">进度：共 {progress.total} 个槽位 · 候选 {progress.candidate} · 已选 {progress.selected} · 已固化 {progress.fixed} · 单句成品 {progress.single}</p>}
    {dialogElement}
  </div>
}
