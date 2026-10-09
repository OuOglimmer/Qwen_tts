'use client'

import type { Notify } from '@/lib/types'
import { Button, SectionTitle } from '@/components/ui'
import { BatchComposer } from './BatchComposer'
import { BatchTable } from './BatchTable'
import { useBatch } from './useBatch'

/** 「配音」页（流程第 2 步）：组合任务域 hook 与两个子组件。 */
export default function BatchPage({ projectId, notify, onNext }: { projectId: string; notify: Notify; onNext?: () => void }) {
  const batch = useBatch(projectId, notify)
  const counts = batch.status?.counts
  const task = batch.status?.task
  const runPct = batch.status?.running && task && task.total
    ? Math.round(((task.succeeded + task.failed) / task.total) * 100)
    : null

  return <>
    <SectionTitle title="绑定声线并批量生成" description="选择已固化声线，按行逐句粘贴台词，加入任务表后在后台批量生成；进度每 2.5 秒自动刷新。" />
    <div className="panel-grid batch-layout">
      <BatchComposer
        voices={batch.voices}
        voiceId={batch.voiceId}
        onVoiceIdChange={batch.setVoiceId}
        status={batch.status}
        editingCount={Object.keys(batch.drafts).length}
        onAdd={batch.add}
        onRun={batch.run}
        onCancel={batch.cancel}
        onClearPending={batch.clearPending}
        onDeletePositions={batch.deletePositions}
        onSaveEdits={batch.saveEdits}
        onRefresh={batch.refresh}
      />
      <div className="wide-content">
        {runPct !== null && <div className="gen-progress">
          <div className="progress-track"><div className="progress-fill" style={{ width: `${runPct}%` }} /></div>
          <span className="hint">本轮进度 {runPct}%（成功 {task?.succeeded ?? 0} · 失败 {task?.failed ?? 0} / 共 {task?.total ?? 0}）</span>
        </div>}
        <p className="progress-line">
          {batch.status?.running
            ? `后台生成中：待处理 ${counts?.pending ?? 0} · 生成中 ${counts?.running ?? 0} · 待选定 ${counts?.completed ?? 0} · 已选入成品 ${counts?.selected ?? 0} · 失败 ${counts?.failed ?? 0}`
            : `任务统计：等待 ${counts?.pending ?? 0} · 待选定 ${counts?.completed ?? 0} · 已选入成品 ${counts?.selected ?? 0} · 失败 ${counts?.failed ?? 0}${task && !task.running ? `（上次：成功 ${task.succeeded}，失败 ${task.failed}）` : ''}。生成完成的音频会自动选入成品，需要剔除时在任务表里取消选定。`}
        </p>
        <BatchTable
          items={batch.items}
          drafts={batch.drafts}
          onDraftChange={batch.setDraft}
          onSelect={batch.selectItem}
          onDelete={batch.deleteRow}
        />
      </div>
    </div>
    {onNext && <div className="button-row">
      <Button primary onClick={onNext}>下一步：试听返修 →</Button>
      <span className="hint">生成完成的音频已自动选入成品，第 3 步可逐条试听与返修。</span>
    </div>}
  </>
}
