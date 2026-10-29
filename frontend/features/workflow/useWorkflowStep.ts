'use client'

import { useEffect, useRef, useState } from 'react'
import { apiPut } from '@/lib/api'
import type { Step } from './steps'

/**
 * 工作流步骤状态（步骤条上移到 header 后提升到组合根）：
 * 进入项目时按「是否新建」决定默认步骤——新建项目落在第 1 步（创建声线），
 * 其余既有项目落在第 2 步（配音生成）；切换步骤仍写入 last-step 做冗余备份。
 * defaultStep 由组合根在切换项目的那一刻一次性确定（见 app/page.tsx 的 openProject），
 * 用 ref 锁定后传入，避免重渲染时把步骤反复重置回默认。
 */
export function useWorkflowStep(projectId: string | null, defaultStep: Step = 2) {
  const [step, setStep] = useState<Step>(defaultStep)
  const restored = useRef(false)

  useEffect(() => {
    restored.current = false
    setStep(defaultStep)
    if (!projectId) return
    // 进入即定位到默认步骤；不再恢复 last-step，遵循产品默认规则。
    restored.current = true
  }, [projectId, defaultStep])

  const changeStep = (next: Step) => {
    setStep(next)
    if (projectId && restored.current) {
      apiPut(`/api/projects/${projectId}/last-step`, { step: next }).catch(() => undefined)
    }
  }

  return { step, changeStep }
}
