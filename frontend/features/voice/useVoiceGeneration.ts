'use client'

import { useRef, useState } from 'react'
import { apiGet, apiPost } from '@/lib/api'
import { usePolling } from '@/lib/hooks'
import type { Notify, VoiceGenTask } from '@/lib/types'
import { writeSeenTask } from './draft'

type UseVoiceGenerationParams = {
  projectId: string
  notify: Notify
  /** 所在页面可见时为 true；隐藏（keep-alive 切走）时暂停轮询，避免后台空转。 */
  active: boolean
  /** 用户当前查看的槽位；任务完成时若用户未手动切走，则自动跳到生成的槽位。 */
  activeRequestId: string | null
  adoptRequest: (requestId: string) => void
  loadCandidates: (requestId: string) => Promise<void>
  loadRequests: () => Promise<unknown>
}

export type GenerateInput = {
  name: string
  prompt: string
  auditionText: string
  language: string
  count: number
  requestId: string | null
}

/** 候选生成任务的生命周期：启动、取消、常驻轮询与完成提示。 */
export function useVoiceGeneration({ projectId, notify, active, activeRequestId, adoptRequest, loadCandidates, loadRequests }: UseVoiceGenerationParams) {
  const [genTask, setGenTask] = useState<VoiceGenTask>(null)
  // 最近一次观察到的生成任务快照，用于识别“未完成 → 完成”跳变，避免重复提示。
  const prevTaskRef = useRef<VoiceGenTask>(null)

  const observe = (task: VoiceGenTask) => {
    prevTaskRef.current = task
    setGenTask(task)
  }

  const cancelGenerate = async () => {
    try {
      await apiPost('/api/voice-generation/cancel')
      notify('已请求取消，当前候选完成后停止')
    } catch (error) { notify((error as Error).message) }
  }

  const generate = async (input: GenerateInput) => {
    try {
      const result = await apiPost<{ started: boolean; task: VoiceGenTask }>(`/api/projects/${projectId}/voice-requests/generate`, {
        name: input.name, prompt: input.prompt, audition_text: input.auditionText, language: input.language, count: input.count, request_id: input.requestId,
      })
      if (result.task) observe(result.task)
      notify('候选生成已开始，逐个生成中…')
      return result.task
    } catch (error) { notify((error as Error).message); return null }
  }

  // 常驻轮询：重进页面后也能接上仍在运行的任务并更新进度条。
  // 实时进度必须绕开 GET 缓存（force），并用 active 在页面隐藏时暂停轮询。
  usePolling(async () => {
    const next = await apiGet<VoiceGenTask>('/api/voice-generation/status', { force: true })
    if (!next || next.project_id !== projectId) return
    const prev = prevTaskRef.current
    // 签名门控：任务结束后后端仍会一直返回同一份快照，内容没变就不
    // setState，避免声线页每 1.5 秒整棵子树空转重渲染。
    if (prev && JSON.stringify(prev) === JSON.stringify(next)) return
    prevTaskRef.current = next
    setGenTask(next)
    if (!next.finished || !prev || prev.finished) return
    if (next.error) notify(`候选生成异常：${next.error}`)
    else if (next.cancelled) notify(`已取消：本轮完成 ${next.generated}/${next.total} 个候选`)
    else notify(`已生成 ${next.generated} 个候选`)
    writeSeenTask(next.request_id)
    // 用户已手动切到其他槽位时不强行跳回。
    if (!activeRequestId || activeRequestId === next.request_id) {
      adoptRequest(next.request_id)
      await loadCandidates(next.request_id)
    }
    await loadRequests()
  }, 1500, active)

  return { genTask, genRunning: Boolean(genTask && !genTask.finished), generate, cancelGenerate, observe }
}
