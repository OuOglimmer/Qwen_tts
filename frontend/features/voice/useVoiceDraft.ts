'use client'

import { useEffect, useRef, useState } from 'react'
import type { VoiceRequest } from '@/lib/types'
import { readDraft, writeDraft } from './draft'

/**
 * 表单字段（名称/提示词/试听文本/语言/候选数）状态 + 按项目持久化。
 * activeRequestId 由页面持有，传入只为参与草稿序列化。
 */
export function useVoiceDraft(projectId: string, activeRequestId: string | null) {
  const [name, setName] = useState('')
  const [prompt, setPrompt] = useState('')
  const [auditionText, setAuditionText] = useState('')
  const [language, setLanguage] = useState('Chinese')
  const [count, setCount] = useState(4)
  // 挂载恢复完成前禁止写草稿，否则空表单会把已存草稿覆盖掉。
  const hydratedRef = useRef(false)

  useEffect(() => {
    const saved = readDraft(projectId)
    if (saved) {
      setName(String(saved.name ?? ''))
      setPrompt(String(saved.prompt ?? ''))
      setAuditionText(String(saved.auditionText ?? ''))
      if (typeof saved.language === 'string' && saved.language) setLanguage(saved.language)
      const draftCount = Number(saved.count)
      if (Number.isFinite(draftCount) && draftCount >= 1) setCount(Math.min(4, Math.trunc(draftCount)))
    }
    hydratedRef.current = true
  }, [projectId])

  useEffect(() => {
    if (!hydratedRef.current) return
    writeDraft(projectId, { activeRequestId, name, prompt, auditionText, language, count })
  }, [projectId, activeRequestId, name, prompt, auditionText, language, count])

  /** 从槽位加载字段（候选与选中态由页面处理）。 */
  const loadFromRequest = (request: VoiceRequest) => {
    setName(request.name)
    setPrompt(request.prompt)
    setAuditionText(request.audition_text)
    setLanguage(request.language)
  }

  return { name, setName, prompt, setPrompt, auditionText, setAuditionText, language, setLanguage, count, setCount, loadFromRequest }
}

export type VoiceDraftState = ReturnType<typeof useVoiceDraft>
