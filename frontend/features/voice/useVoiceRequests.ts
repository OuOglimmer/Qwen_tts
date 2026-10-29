'use client'

import { useCallback, useState } from 'react'
import { apiGet, apiPost } from '@/lib/api'
import type { VoiceRequest } from '@/lib/types'

/** 声线槽位列表 + 项目进度统计 + 批量建槽。 */
export function useVoiceRequests(projectId: string) {
  const [requests, setRequests] = useState<VoiceRequest[]>([])
  const [progress, setProgress] = useState<Record<string, number> | null>(null)

  const fetchRequests = useCallback(async () => {
    return apiGet<VoiceRequest[]>(`/api/projects/${projectId}/voice-requests`)
  }, [projectId])

  const loadProgress = useCallback(async () => {
    setProgress(await apiGet<Record<string, number>>(`/api/projects/${projectId}/voice-progress`))
  }, [projectId])

  const loadRequests = useCallback(async () => {
    const rows = await fetchRequests()
    setRequests(rows)
    await loadProgress()
    return rows
  }, [fetchRequests, loadProgress])

  /** 批量建空槽位（status=draft）：名单由调用方给定，后端只负责落库与去重兜底。 */
  const createSlots = useCallback(async (names: string[], language: string) => {
    const result = await apiPost<{ created: string[]; items: VoiceRequest[] }>(`/api/projects/${projectId}/voice-requests`, {
      names_text: names.join('\n'),
      language,
    })
    // 不能直接 setRequests(result.items)：那是数据库原始行，没有 tag_list/tag_ids，
    // 用它覆盖会让已有的槽位标签列当场变空。统一走 loadRequests 拿带标签的完整行。
    await loadRequests()
    return result.created
  }, [projectId, loadRequests])

  return { requests, setRequests, progress, fetchRequests, loadProgress, loadRequests, createSlots }
}
