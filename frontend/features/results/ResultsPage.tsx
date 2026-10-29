'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { apiGet, apiPost } from '@/lib/api'
import { fetchActiveVoices } from '@/lib/voices'
import { ALL_ROLES, activeVoiceIdOfRole, filterByRole, groupRoles, resolveRole } from '@/lib/roles'
import type { BatchItem, LineEdit, Notify, VoiceAsset } from '@/lib/types'
import { Button, RoleSelect, SectionTitle } from '@/components/ui'
import { ResultRow } from './ResultRow'
import { VoiceRegenPanel } from './VoiceRegenPanel'

/** 「试听返修」页（流程第 3 步）：逐条返修 + 按人物批量返修。
 *  「单句成品」盒子已移除（2026-10-28）：这一页只处理本轮的配音结果，
 *  第 1 步候选卡上的「单句成品」仍可保留，成品页仍能查看与删除。 */
export default function ResultsPage({ projectId, notify, active, onNext }: { projectId: string; notify: Notify; active?: boolean; onNext?: () => void }) {
  const [completed, setCompleted] = useState<BatchItem[]>([])
  const [edits, setEdits] = useState<Record<string, LineEdit>>({})
  const [voices, setVoices] = useState<VoiceAsset[]>([])
  const [busyItem, setBusyItem] = useState<string | null>(null)
  // 按角色筛选只影响"看哪几行"：序号、重新生成、取消选定全都按条目本身走，与筛选无关。
  const [role, setRole] = useState<string>(ALL_ROLES)

  const refresh = useCallback(async () => {
    const [batchRows, activeVoices] = await Promise.all([
      apiGet<BatchItem[]>(`/api/projects/${projectId}/batch`),
      fetchActiveVoices(projectId),
    ])
    setCompleted(batchRows.filter((item) => item.output_url && (item.status === 'completed' || item.status === 'selected')))
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

  // 「按人物批量返修」只列本项目真的生成过配音的声线：清单里的声线没出过片子，
  // 对它点返修等于空转（后端找不到可重录的句子），却让人以为页面漏了东西。
  const voiceIdsWithAudio = new Set(completed.map((item) => item.voice_id))
  const regenVoices = voices.filter((voice) => voiceIdsWithAudio.has(voice.id))

  // 角色条只列"已经有成品"的角色（与上面 regenVoices 同一个理由：没出过片子点进去是空白），
  // 顺序按台词首次出现。筛选只改显示，不动任何条目状态。
  const roles = useMemo(() => groupRoles(completed), [completed])
  const activeRole = resolveRole(role, roles)
  const visibleRows = useMemo(() => filterByRole(completed, activeRole), [completed, activeRole])
  // 角色被删空后回落「全部」，state 也要跟着回来，否则它只是"暂时不存在"：
  // 等该角色重新出片子时页面会自己跳回那个筛选（与第 2 步同一个处理）。
  useEffect(() => { if (role !== activeRole) setRole(activeRole) }, [role, activeRole])
  // 选中某个角色 = 接下来要返修这个角色，下方「按人物批量返修」的下拉直接跟过去。
  const preferredVoiceId = activeVoiceIdOfRole(activeRole, voices) ?? undefined

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

  /** 换用历史版本：后端把那一版复制成新的一版（版本号继续加一），旧版仍留档。 */
  const useVersion = async (item: BatchItem, version: number) => {
    try {
      const result = await apiPost<{ version: number; restored_from: number }>(
        `/api/projects/${projectId}/batch/${item.id}/use-version`, { version },
      )
      // 丢掉这行的文本草稿：旧版的台词可能和现在输入框里的不一样，留着会音字不符。
      setEdits((previous) => {
        const next = { ...previous }
        delete next[item.id]
        return next
      })
      await refresh()
      notify(`第 ${item.position} 条已换用 v${result.restored_from}（记为新的一版 v${result.version}，旧版仍可再换回）`)
    } catch (error) { notify((error as Error).message) }
  }

  return <>
    <SectionTitle number="3" title="试听返修" description="批量结果已自动选入成品；逐条试听，行内改文本或语言后重新生成，不要的一行点「取消选定」移出成品。" />
    <div className="step-stack">
      {/* 用 products-panel（= .panel-flush 的旧名）：ResultRow 里那几行下拉/文本域宽度规则
          是挂在 .products-panel 后代上的，换类名会让台词框被下拉挤成一条。 */}
      <div className="panel products-panel">
        {/* 角色下拉在标题栏右侧：只切"看哪几行"，序号、重新生成、取消选定都按条目本身走。 */}
        <div className="panel-heading">配音结果
          <RoleSelect roles={roles} total={completed.length} value={activeRole} onChange={setRole} />
          {/* 计数常驻（宽度也钉死）：筛完才冒出来的话，左边的下拉会被顶得往左跳一格。 */}
          <span>{activeRole === ALL_ROLES ? `共 ${completed.length} 条` : `筛选 ${visibleRows.length} / 共 ${completed.length} 条`}</span>
        </div>
        {completed.length === 0 && <p className="empty-state">暂无结果。到第 2 步「配音生成」批量生成后，这里逐条显示。</p>}
        {completed.length > 0 && visibleRows.length === 0 && <p className="empty-state">「{activeRole}」暂无结果，在角色下拉里选「全部角色」看完整列表。</p>}
        {visibleRows.map((item) => <ResultRow
          key={item.id}
          item={item}
          edit={edits[item.id] ?? { text: item.text, language: item.language }}
          busy={busyItem === item.id}
          onEditChange={(edit) => setEdits({ ...edits, [item.id]: edit })}
          onRegenerate={() => regenerateItem(item)}
          onSelect={(selected) => selectItem(item, selected)}
          onUseVersion={(version) => useVersion(item, version)}
        />)}
      </div>
      <VoiceRegenPanel projectId={projectId} voices={regenVoices} notify={notify} active={active} onRegenerated={refresh} preferredVoiceId={preferredVoiceId} />
      {onNext && <div className="panel step-footer">
        <Button primary onClick={onNext}>下一步：成品导出 →</Button>
        <span className="hint">已选入成品的结果会进入第 4 步并参与导出；点「取消选定」的结果不会导出。</span>
      </div>}
    </div>
  </>
}
