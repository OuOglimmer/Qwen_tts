'use client'

import { useState } from 'react'
import { Button, Label, LanguageSelect, useConfirmDialog, VoiceSelect } from '@/components/ui'
import type { BatchStatus, VoiceAsset } from '@/lib/types'

type BatchComposerProps = {
  voices: VoiceAsset[]
  voiceId: string
  onVoiceIdChange: (voiceId: string) => void
  status: BatchStatus | null
  editingCount: number
  onAdd: (text: string, language: string) => Promise<number | null>
  onRun: () => void
  onCancel: () => void
  onClearPending: () => void
  onDeletePositions: (positionsInput: string) => Promise<boolean>
  onSaveEdits: () => void
  onRefresh: () => void
}

/** 左侧控制面板：声线绑定、台词录入与任务管理工具。 */
export function BatchComposer({ voices, voiceId, onVoiceIdChange, status, editingCount, onAdd, onRun, onCancel, onClearPending, onDeletePositions, onSaveEdits, onRefresh }: BatchComposerProps) {
  const [text, setText] = useState('')
  const [language, setLanguage] = useState('Chinese')
  const [positionsInput, setPositionsInput] = useState('')
  const { confirm, dialogElement } = useConfirmDialog()

  const add = async () => {
    const added = await onAdd(text, language)
    if (added !== null) setText('')
  }

  const deletePositions = async () => {
    if (await onDeletePositions(positionsInput)) setPositionsInput('')
  }

  const clearPending = async () => {
    if (!(await confirm({
      title: '清除未完成任务',
      message: '确认清除所有未完成（等待/失败）任务？已生成的音频不受影响。',
      confirmText: '清除',
      danger: true,
    }))) return
    onClearPending()
  }

  return <div className="control-panel">
    <Label>绑定声线（仅显示已固化）</Label>
    <VoiceSelect
      voices={voices}
      value={voiceId}
      onChange={onVoiceIdChange}
      placeholder="暂无已固化声线，请先到「声线」页固化"
    />
    <Label>语言</Label>
    <LanguageSelect value={language} onChange={setLanguage} />
    <Label>台词文本</Label>
    <textarea className="tall" value={text} onChange={(event) => setText(event.target.value)} placeholder="一行对应一个音频文件。空行会被忽略。" />
    <div className="button-row">
      <Button onClick={add}>加入任务</Button>
      <Button primary onClick={onRun} disabled={Boolean(status?.running)}>开始/继续批量生成</Button>
    </div>
    <div className="panel task-tools">
      <div className="panel-heading">⚒ 任务管理</div>
      <div className="button-row">
        <Button onClick={onRefresh}>刷新任务表</Button>
        <Button onClick={onSaveEdits} disabled={editingCount === 0}>保存修改</Button>
      </div>
      <div className="button-row">
        <Button onClick={onCancel} disabled={!status?.running}>取消当前任务</Button>
        <Button onClick={clearPending}>清除未完成任务</Button>
      </div>
      <Label>批量删除序号</Label>
      <div className="button-row">
        <input value={positionsInput} onChange={(event) => setPositionsInput(event.target.value)} placeholder="例如：1,3,5~8（支持区间）" />
        <Button onClick={deletePositions}>删除</Button>
      </div>
    </div>
    {dialogElement}
  </div>
}
