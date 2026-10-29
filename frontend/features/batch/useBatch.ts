'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { apiDelete, apiGet, apiPost } from '@/lib/api'
import { usePolling } from '@/lib/hooks'
import { parseIndexSpec } from '@/lib/indexSpec'
import { fetchActiveVoices } from '@/lib/voices'
import type { BatchItem, BatchStatus, LineEdit, Notify, VoiceAsset } from '@/lib/types'

/** 配音任务域：可绑定声线、任务表、后台任务状态与全部操作。 */
export function useBatch(projectId: string, notify: Notify, active: boolean = true) {
  const [voices, setVoices] = useState<VoiceAsset[]>([])
  const [items, setItems] = useState<BatchItem[]>([])
  const [status, setStatus] = useState<BatchStatus | null>(null)
  const [drafts, setDrafts] = useState<Record<number, LineEdit>>({})
  const [voiceId, setVoiceId] = useState('')
  const wasRunning = useRef(false)
  const statusSignature = useRef('')

  const refresh = useCallback(async () => {
    const [activeVoices, batchRows] = await Promise.all([
      fetchActiveVoices(projectId),
      apiGet<BatchItem[]>(`/api/projects/${projectId}/batch`),
    ])
    setVoices(activeVoices)
    setItems(batchRows)
    if (!voiceId && activeVoices[0]) setVoiceId(activeVoices[0].id)
  }, [projectId, voiceId])

  const refreshStatus = useCallback(async (force = false) => {
    const next = await apiGet<BatchStatus>(`/api/projects/${projectId}/batch/status`, { force })
    // 运行/计数/任务计数器都没变化时不再 setState，避免闲置时整页每 2.5 秒重渲染。
    const signature = JSON.stringify([next.running, next.counts, next.task])
    const changed = signature !== statusSignature.current
    if (changed) {
      statusSignature.current = signature
      setStatus(next)
    }
    const finished = wasRunning.current && !next.running
    wasRunning.current = next.running
    if (finished) {
      // 收尾先拉一次最终任务表：最后一条的结果要立刻出现在页面上，而不是等下次手动刷新。
      await refresh()
      const task = next.task
      if (task?.error) notify(`批量任务异常结束：${task.error}`)
      else if (task?.cancelled) notify(`批量已取消：成功 ${task.succeeded}，失败 ${task.failed}`)
      else if (task) notify(`批量完成：成功 ${task.succeeded}，失败 ${task.failed}`)
      return
    }
    // 生成期间只在计数变化（有任务完成/失败）时才重拉任务表。
    if (next.running && changed) await refresh()
  }, [projectId, notify, refresh])

  useEffect(() => {
    wasRunning.current = false
    refresh().catch((error: Error) => notify(error.message))
    refreshStatus().catch(() => undefined)
    // key={projectId} 保证换项目时整页重挂载，这里只在挂载/换项目时执行。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  // 实时进度绕开 GET 缓存（force:true），并在页面隐藏（keep-alive 切走）时暂停轮询。
  usePolling(() => refreshStatus(true), 2500, active)

  const setDraft = (position: number, draft: LineEdit) => {
    setDrafts((previous) => ({ ...previous, [position]: draft }))
  }

  /** 加入台词；成功返回新增条数（供表单清空输入），失败返回 null。 */
  const add = async (text: string, language: string) => {
    if (!voiceId) { notify('请先选择已固化的声线'); return null }
    try {
      const result = await apiPost<{ added: number }>(`/api/projects/${projectId}/batch`, { voice_id: voiceId, language, text })
      await refresh()
      notify(`已加入 ${result.added} 条台词`)
      return result.added
    } catch (error) { notify((error as Error).message); return null }
  }

  const run = async () => {
    try { await apiPost(`/api/projects/${projectId}/batch/run`); wasRunning.current = false; notify('批量生成已开始，后台执行中…') } catch (error) { notify((error as Error).message) }
  }

  const cancel = async () => {
    try { await apiPost(`/api/projects/${projectId}/batch/cancel`); notify('已请求取消，正在收尾…') } catch (error) { notify((error as Error).message) }
  }

  const clearPending = async () => {
    try { await apiPost(`/api/projects/${projectId}/batch/clear`); await refresh(); notify('未完成任务已清除') } catch (error) { notify((error as Error).message) }
  }

  /** 解析并删除输入的序号（支持 1,3,5~8 区间写法）；成功返回 true 供输入框清空。 */
  const deletePositions = async (positionsInput: string) => {
    const maxPosition = items.length ? items[items.length - 1].position : 0
    const parsed = parseIndexSpec(positionsInput, maxPosition)
    if (typeof parsed === 'string') { notify(parsed); return false }
    try {
      await apiPost(`/api/projects/${projectId}/batch/delete`, { positions: parsed })
      await refresh()
      notify(`已删除 ${parsed.length} 条任务`)
      return true
    } catch (error) { notify((error as Error).message); return false }
  }

  const deleteRow = async (position: number) => {
    try { await apiDelete(`/api/projects/${projectId}/batch/${position}`); await refresh() } catch (error) { notify((error as Error).message) }
  }

  const selectItem = async (item: BatchItem, selected: boolean) => {
    try {
      await apiPost(`/api/projects/${projectId}/batch/${item.id}/select`, { selected })
      await refresh()
      notify(selected ? `第 ${item.position} 条已选入成品，可在「成品导出」查看` : `第 ${item.position} 条已取消选入`)
    } catch (error) { notify((error as Error).message) }
  }

  const saveEdits = async () => {
    const changed = Object.entries(drafts)
      .map(([position, value]) => ({ position: Number(position), text: value.text.trim(), language: value.language }))
      .filter((item) => item.text)
    if (changed.length === 0) { notify('没有待保存的修改'); return }
    try {
      const result = await apiPost<{ updated: number }>(`/api/projects/${projectId}/batch/save-edits`, { items: changed })
      setDrafts({})
      await refresh()
      notify(`已保存 ${result.updated} 条修改`)
    } catch (error) { notify((error as Error).message) }
  }

  return {
    voices, items, status, drafts, voiceId, setVoiceId, setDraft, refresh,
    add, run, cancel, clearPending, deletePositions, deleteRow, selectItem, saveEdits,
  }
}
