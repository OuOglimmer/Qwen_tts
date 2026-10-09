'use client'

import { useCallback, useEffect, useState } from 'react'
import { apiDelete, apiGet, apiPost } from '@/lib/api'
import { fetchActiveVoices } from '@/lib/voices'
import type { BatchItem, LineEdit, Notify, SingleOutput, VoiceAsset } from '@/lib/types'
import { Button, SectionTitle, useConfirmDialog } from '@/components/ui'
import { ResultRow } from './ResultRow'
import { SingleOutputsPanel } from './SingleOutputsPanel'
import { VoiceRegenPanel } from './VoiceRegenPanel'

/** 「试听返修」页（流程第 3 步）：逐条返修 + 按人物批量返修 + 单句成品管理。 */
export default function ResultsPage({ projectId, notify, onNext }: { projectId: string; notify: Notify; onNext?: () => void }) {
  const [completed, setCompleted] = useState<BatchItem[]>([])
  const [edits, setEdits] = useState<Record<string, LineEdit>>({})
  const [singles, setSingles] = useState<SingleOutput[]>([])
  const [voices, setVoices] = useState<VoiceAsset[]>([])
  const [busyItem, setBusyItem] = useState<string | null>(null)
  const { confirm, dialogElement } = useConfirmDialog()

  const refresh = useCallback(async () => {
    const [batchRows, singleRows, activeVoices] = await Promise.all([
      apiGet<BatchItem[]>(`/api/projects/${projectId}/batch`),
      apiGet<SingleOutput[]>(`/api/projects/${projectId}/single-outputs`),
      fetchActiveVoices(projectId),
    ])
    setCompleted(batchRows.filter((item) => item.output_url && (item.status === 'completed' || item.status === 'selected')))
    setSingles(singleRows)
    setVoices(activeVoices)
    // 只保留现存行的草稿：删除/重排后旧 key 不残留（切换项目时整个组件重挂载清空）。
    setEdits((previous) => {
      const next: Record<string, LineEdit> = {}
      for (const item of batchRows) {
        next[item.id] = previous[item.id] ?? { text: item.text, language: item.language }
      }
      return next
    })
  }, [projectId])

  useEffect(() => { refresh().catch((error: Error) => notify(error.message)) }, [projectId])

  const regenerateItem = async (item: BatchItem) => {
    const edit = edits[item.id] ?? { text: item.text, language: item.language }
    if (!edit.text.trim()) { notify('台词不能为空'); return }
    setBusyItem(item.id)
    try {
      await apiPost(`/api/projects/${projectId}/batch/${item.id}/regenerate`, { text: edit.text, language: edit.language })
      await refresh(); notify(`第 ${item.position} 条已重新生成并自动选入成品`)
    } catch (error) { notify((error as Error).message) } finally { setBusyItem(null) }
  }

  const selectItem = async (item: BatchItem, selected: boolean) => {
    try {
      await apiPost(`/api/projects/${projectId}/batch/${item.id}/select`, { selected })
      await refresh()
      notify(selected ? `第 ${item.position} 条已选入成品` : `第 ${item.position} 条已取消选入`)
    } catch (error) { notify((error as Error).message) }
  }

  const deleteSingle = async (row: SingleOutput) => {
    if (!(await confirm({
      title: '删除单句成品',
      message: `确认删除单句成品「${row.name}」？候选与固化声线不受影响。`,
      confirmText: '删除',
      danger: true,
    }))) return
    try { await apiDelete(`/api/projects/${projectId}/single-outputs/${row.id}`); await refresh(); notify('单句成品已删除') } catch (error) { notify((error as Error).message) }
  }

  return <>
    <SectionTitle number="3" title="试听返修" description="批量结果已自动选入成品；逐条试听，行内改文本或语言后重新生成，不要的一行点「取消选定」移出成品。" />
    <div className="panel products-panel">
      <div className="panel-heading">配音结果 <span>{completed.length} 条</span></div>
      {completed.length === 0 && <p className="empty-state">暂无结果。到第 2 步「配音生成」批量生成后，这里逐条显示。</p>}
      {completed.map((item) => <ResultRow
        key={item.id}
        item={item}
        edit={edits[item.id] ?? { text: item.text, language: item.language }}
        busy={busyItem === item.id}
        onEditChange={(edit) => setEdits({ ...edits, [item.id]: edit })}
        onRegenerate={() => regenerateItem(item)}
        onSelect={(selected) => selectItem(item, selected)}
      />)}
    </div>
    <div className="sub-panel-row">
      <VoiceRegenPanel projectId={projectId} voices={voices} notify={notify} onRegenerated={refresh} />
      <SingleOutputsPanel singles={singles} onDelete={deleteSingle} />
    </div>
    {onNext && <div className="button-row">
      <Button primary onClick={onNext}>下一步：成品导出 →</Button>
      <span className="hint">已选入成品的结果和单句成品会进入第 4 步并参与导出；取消选定的结果不会导出。</span>
    </div>}
    {dialogElement}
  </>
}
