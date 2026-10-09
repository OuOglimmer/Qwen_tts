'use client'

import { useCallback, useState } from 'react'
import { apiGet } from '@/lib/api'
import type { Candidate } from '@/lib/types'

/** 当前槽位的候选与暂存列表。 */
export function useVoiceCandidates() {
  const [candidates, setCandidates] = useState<Candidate[]>([])
  const [shortlisted, setShortlisted] = useState<Candidate[]>([])

  const loadCandidates = useCallback(async (requestId: string | null) => {
    if (!requestId) { setCandidates([]); setShortlisted([]); return }
    const payload = await apiGet<{ candidates: Candidate[]; shortlisted: Candidate[] }>(`/api/voice-requests/${requestId}/candidates`)
    setCandidates(payload.candidates)
    setShortlisted(payload.shortlisted)
  }, [])

  const clear = useCallback(() => { setCandidates([]); setShortlisted([]) }, [])

  return { candidates, shortlisted, loadCandidates, clear }
}
