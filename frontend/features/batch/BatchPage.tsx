'use client'

import { useEffect, useMemo, useState } from 'react'
import type { Notify } from '@/lib/types'
import { ALL_ROLES, activeVoiceIdOfRole, filterByRole, groupRoles, resolveRole } from '@/lib/roles'
import { Button, RoleSelect, SectionTitle } from '@/components/ui'
import { BatchComposer } from './BatchComposer'
import { BatchTable } from './BatchTable'
import { useBatch } from './useBatch'

/** 「配音」页（流程第 2 步）：组合任务域 hook 与两个子组件。 */
export default function BatchPage({ projectId, notify, active, onNext }: { projectId: string; notify: Notify; active?: boolean; onNext?: () => void }) {
  const batch = useBatch(projectId, notify, active ?? true)
  // 按角色筛选只影响"看哪几行"，不改任何数据：序号仍是全局的，批量删除仍按全局序号。
  const [role, setRole] = useState<string>(ALL_ROLES)
  const roles = useMemo(() => groupRoles(batch.items), [batch.items])
  // 角色被删空（行全删完、换项目）时回落「全部」，否则会停在一张空表上。
  const activeRole = resolveRole(role, roles)
  const visibleItems = useMemo(() => filterByRole(batch.items, activeRole), [batch.items, activeRole])
  // 角色被删空后回落「全部」，state 也要跟着回来：否则它是"暂时不存在"而不是"改回全部"，
  // 等这个角色又出现（重新加台词/重新生成）时页面会自己跳回那个筛选，让人莫名其妙。
  useEffect(() => { if (role !== activeRole) setRole(activeRole) }, [role, activeRole])

  const counts = batch.status?.counts
  const task = batch.status?.task
  const runPct = batch.status?.running && task && task.total
    ? Math.round(((task.succeeded + task.failed) / task.total) * 100)
    : null

  /** 选定角色 = 接下来要录这个角色的台词，左侧绑定声线跟过去，省一次手动选。 */
  const changeRole = (next: string) => {
    setRole(next)
    const voiceId = activeVoiceIdOfRole(next, batch.voices)
    if (voiceId) batch.setVoiceId(voiceId)
  }

  return <>
    <SectionTitle number="2" title="绑定声线并批量生成" description="选择已固化声线，按行逐句粘贴台词，加入任务表后在后台批量生成；进度每 2.5 秒自动刷新。" />
    <div className="step-stack">
      <div className="panel-grid batch-layout">
        <BatchComposer
          projectId={projectId}
          notify={notify}
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
        {/* 右栏原来是裸容器：进度文字和空态直接浮在页面底色上，与左栏卡片不成对。
            现在整块收进列表型大盒子，标题栏 + 进度 + 表格同一张卡。 */}
        <div className="panel panel-flush">
          {/* 角色下拉挂标题栏右侧（与计数胶囊同侧）：它是"看哪几行"的开关，不必单占一条横带。
              只有一个角色时整条不渲染，标题栏只剩标题 + 计数。 */}
          <div className="panel-heading">任务表
            <RoleSelect roles={roles} total={batch.items.length} value={activeRole} onChange={changeRole} />
            {/* 计数常驻（宽度也钉死）：筛完才冒出来的话，左边的下拉会被顶得往左跳一格。 */}
            <span>{activeRole === ALL_ROLES ? `共 ${batch.items.length} 条` : `筛选 ${visibleItems.length} / 共 ${batch.items.length} 条`}</span>
          </div>
          <div className="panel-inset">
            {/* 统计块只对"多条"有意义：只有一条台词时，等待/待选定/失败 全是 0，纯噪声。
                块高钉死在 .task-stats（66px）：进度条改为常驻 + 空闲时 visibility:hidden，
                文案在两行/一行之间切换也不再带着盒子一起抽动。
                注意：这里的数字**始终是全局的**，不跟着角色筛选变 —— 批量是一条流水线在跑，
                按角色切会让「共 N 条」变得不可信。 */}
            {batch.items.length > 1 && <div className="task-stats">
              <div className={batch.status?.running ? 'task-stats-bar' : 'task-stats-bar is-idle'}>
                <div className="progress-track"><div className="progress-fill" style={{ width: `${runPct ?? 0}%` }} /></div>
                <span className="hint">本轮进度 {runPct ?? 0}%（成功 {task?.succeeded ?? 0} · 失败 {task?.failed ?? 0} / 共 {task?.total ?? 0}）</span>
              </div>
              <p className="progress-line">
                {batch.status?.running
                  ? `后台生成中（全局）：待处理 ${counts?.pending ?? 0} · 生成中 ${counts?.running ?? 0} · 待选定 ${counts?.completed ?? 0} · 已选入成品 ${counts?.selected ?? 0} · 失败 ${counts?.failed ?? 0}`
                  : `全局统计：等待 ${counts?.pending ?? 0} · 待选定 ${counts?.completed ?? 0} · 已选入成品 ${counts?.selected ?? 0} · 失败 ${counts?.failed ?? 0}${task && !task.running ? `（上次：成功 ${task.succeeded}，失败 ${task.failed}）` : ''}。生成完成的音频会自动选入成品，需要剔除时在任务表里取消选定。`}
              </p>
            </div>}
          </div>
          <BatchTable
            items={visibleItems}
            drafts={batch.drafts}
            empty={activeRole === ALL_ROLES ? '暂无数据' : `「${activeRole}」暂无任务，在角色下拉里选「全部角色」看完整任务表`}
            onDraftChange={batch.setDraft}
            onSelect={batch.selectItem}
            onDelete={batch.deleteRow}
          />
        </div>
      </div>
      {onNext && <div className="panel step-footer">
        <Button primary onClick={onNext}>下一步：试听返修 →</Button>
        <span className="hint">生成完成的音频已自动选入成品，第 3 步可逐条试听与返修。</span>
      </div>}
    </div>
  </>
}
