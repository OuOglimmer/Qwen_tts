'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import type { VoiceRequest } from '@/lib/types'
import { readDraft, writeDraft } from './draft'

/** 一轮候选数上限，与后端 CandidatePayload 的 count le=8 对齐。 */
export const MAX_CANDIDATES = 8

/**
 * 表单字段（名称/提示词/试听文本/语言/候选数）状态 + 按项目持久化。
 * activeRequestId 由页面持有，传入只为参与草稿序列化。
 */
export function useVoiceDraft(projectId: string, activeRequestId: string | null) {
  const [name, setName] = useState('')
  const [prompt, setPrompt] = useState('')
  const [auditionText, setAuditionText] = useState('')
  const [language, setLanguage] = useState('Chinese')
  const [count, setCountValue] = useState(4)
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
      if (Number.isFinite(draftCount) && draftCount >= 1) setCountValue(Math.min(MAX_CANDIDATES, Math.trunc(draftCount)))
    }
    hydratedRef.current = true
  }, [projectId])

  useEffect(() => {
    if (!hydratedRef.current) return
    writeDraft(projectId, { activeRequestId, name, prompt, auditionText, language, count })
  }, [projectId, activeRequestId, name, prompt, auditionText, language, count])

  /** 夹到 1~MAX_CANDIDATES：不夹取的话，手输超限值会直接吃到后端的 422。 */
  const setCount = useCallback((value: number) => {
    setCountValue(Math.min(MAX_CANDIDATES, Math.max(1, Math.trunc(value) || 1)))
  }, [])

  /** 从槽位加载字段（候选与选中态由页面处理）。 */
  const loadFromRequest = (request: VoiceRequest) => {
    setName(request.name)
    setPrompt(request.prompt)
    setAuditionText(request.audition_text)
    setLanguage(request.language)
  }

  /** 切回「＋ 新声线」时的对称复位：槽位身份相关的三项清空，语言与候选数是跨槽位偏好，保持不动。 */
  const resetForNewVoice = useCallback(() => {
    setName('')
    setPrompt('')
    setAuditionText('')
  }, [])

  return { name, setName, prompt, setPrompt, auditionText, setAuditionText, language, setLanguage, count, setCount, loadFromRequest, resetForNewVoice }
}

export type VoiceDraftState = ReturnType<typeof useVoiceDraft>
