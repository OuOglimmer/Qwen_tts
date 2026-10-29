'use client'

import { useEffect, useRef, useState } from 'react'
import { Button, Label, LanguageSelect, useConfirmDialog, VoiceSelect } from '@/components/ui'
import type { BatchStatus, Notify, VoiceAsset } from '@/lib/types'
import { AddLibraryVoice } from './AddLibraryVoice'

type BatchComposerProps = {
  projectId: string
  notify: Notify
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
export function BatchComposer({ projectId, notify, voices, voiceId, onVoiceIdChange, status, editingCount, onAdd, onRun, onCancel, onClearPending, onDeletePositions, onSaveEdits, onRefresh }: BatchComposerProps) {
  const [text, setText] = useState('')
  const [language, setLanguage] = useState('Chinese')
  const [positionsInput, setPositionsInput] = useState('')
  const { confirm, dialogElement } = useConfirmDialog()
  // 语言默认跟所选声线一致：voices.language 就是创建这把声线时用的语种，
  // 用中文声线读英文台词是要额外手动改的例外，默认值不该反过来。
  // appliedVoiceRef 记住"已经对齐过哪把声线"——voices 每次刷新都是新数组引用，
  // 不加这道门就会把用户手动改过的语言反复按回声线语种。
  const appliedVoiceRef = useRef<string | null>(null)
  useEffect(() => {
    if (!voiceId) return
    const voice = voices.find((item) => item.id === voiceId)
    if (!voice?.language || appliedVoiceRef.current === voiceId) return
    appliedVoiceRef.current = voiceId
    setLanguage(voice.language)
  }, [voiceId, voices])

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
    <div className="label-row">
      <Label>绑定声线（本项目清单内的有效声线）</Label>
      <AddLibraryVoice projectId={projectId} rosterIds={voices.map((voice) => voice.id)} notify={notify} onAdded={onRefresh} />
    </div>
    <VoiceSelect
      voices={voices}
      value={voiceId}
      onChange={onVoiceIdChange}
      placeholder="清单暂无声线，请先固化或用上方按钮从声线库添加"
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
      <div className="button-row delete-positions-row">
        <input value={positionsInput} onChange={(event) => setPositionsInput(event.target.value)} placeholder="例如：1,3,5~8（支持区间）" />
        <Button onClick={deletePositions}>删除</Button>
      </div>
      {/* 任务表按角色筛选后序号会跳号（那本来就是第 7 条台词），这里删除仍按全局编号解析，
          说一句省得人以为筛完要重新数。 */}
      <p className="hint delete-positions-hint">序号按任务表全局编号，不受右侧角色筛选影响。</p>
    </div>
    {dialogElement}
  </div>
}
