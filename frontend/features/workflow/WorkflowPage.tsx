'use client'

import { useState } from 'react'
import type { Notify } from '@/lib/types'
import BatchPage from '@/features/batch/BatchPage'
import ProductsPage from '@/features/products/ProductsPage'
import ResultsPage from '@/features/results/ResultsPage'
import VoiceWorkbench from '@/features/voice/VoiceWorkbench'

const STEPS = [
  { key: 1, label: '创建声线' },
  { key: 2, label: '配音生成' },
  { key: 3, label: '试听返修' },
  { key: 4, label: '成品导出' },
] as const

type Step = (typeof STEPS)[number]['key']

/**
 * 声线工作流壳：只维护当前步骤，四个步骤复用各独立页面的组件
 * （BatchPage/ProductsPage 同时也是侧边栏独立页面，onNext 才是流程态差异）。
 */
export default function WorkflowPage({ projectId, projectName, notify }: { projectId: string; projectName?: string; notify: Notify }) {
  const [step, setStep] = useState<Step>(1)

  return <div className="workflow-page">
    <nav className="workflow-nav" aria-label="声线创作步骤">
      {STEPS.map((item) => (
        <button key={item.key} className={step === item.key ? 'active' : ''} onClick={() => setStep(item.key)}>
          <span>{item.key}</span>{item.label}
        </button>
      ))}
    </nav>
    <section className="workflow-step">
      {step === 1 && <VoiceWorkbench projectId={projectId} notify={notify} onNext={() => setStep(2)} />}
      {step === 2 && <BatchPage projectId={projectId} notify={notify} onNext={() => setStep(3)} />}
      {step === 3 && <ResultsPage projectId={projectId} notify={notify} onNext={() => setStep(4)} />}
      {step === 4 && <ProductsPage projectId={projectId} projectName={projectName ?? '当前项目'} notify={notify} />}
    </section>
  </div>
}
