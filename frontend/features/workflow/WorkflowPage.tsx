'use client'

import type { Notify } from '@/lib/types'
import BatchPage from '@/features/batch/BatchPage'
import ProductsPage from '@/features/products/ProductsPage'
import ResultsPage from '@/features/results/ResultsPage'
import VoiceWorkbench from '@/features/voice/VoiceWorkbench'
import type { Step } from './steps'

/**
 * 声线工作流内容区：只负责渲染当前步骤对应的页面组件（BatchPage/ProductsPage
 * 同时也是别的入口页组件，onNext 才是流程态差异）。
 * 步骤条与 step 状态已上移到 header（useWorkflowStep + 组合根），经 props 注入。
 */
export default function WorkflowPage({ projectId, projectName, step, onStepChange, notify, active }: {
  projectId: string
  projectName?: string
  step: Step
  onStepChange: (next: Step) => void
  notify: Notify
  /** 工作台被切走（hidden）时为 false，向下透传以暂停子页轮询。 */
  active?: boolean
}) {
  return <section className="workflow-step">
    {step === 1 && <VoiceWorkbench projectId={projectId} notify={notify} active={active} onNext={() => onStepChange(2)} />}
    {step === 2 && <BatchPage projectId={projectId} notify={notify} active={active} onNext={() => onStepChange(3)} />}
    {step === 3 && <ResultsPage projectId={projectId} notify={notify} active={active} onNext={() => onStepChange(4)} />}
    {step === 4 && <ProductsPage projectId={projectId} projectName={projectName ?? '当前项目'} notify={notify} active={active} />}
  </section>
}
