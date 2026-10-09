'use client'

/**
 * 组合根（唯一有全局状态的组件）：
 * - 导航状态（当前页）留在这里；
 * - 项目域状态收敛到 useProjects；
 * - 轻提示收敛到 useToast；
 * - 各页面按领域拆在 features/ 下，通过 props 领取 projectId/notify。
 * key={projectId}：切换项目时强制重挂载页面，页面内状态随之复位。
 */
import { useState } from 'react'
import { Toast } from '@/components/ui'
import { useToast } from '@/lib/hooks'
import type { Page } from '@/lib/nav'
import { AssetsPage } from '@/features/assets/AssetsPage'
import { UploadVoicePage } from '@/features/assets/UploadVoicePage'
import BatchPage from '@/features/batch/BatchPage'
import { useProjects } from '@/features/projects/useProjects'
import ProjectsPage from '@/features/projects/ProjectsPage'
import ProductsPage from '@/features/products/ProductsPage'
import ResultsPage from '@/features/results/ResultsPage'
import { ProjectSwitcher } from '@/features/shell/ProjectSwitcher'
import { Sidebar } from '@/features/shell/Sidebar'
import SystemPage from '@/features/system/SystemPage'
import ToolPage, { type ToolType } from '@/features/tools/ToolPage'
import VoiceWorkbench from '@/features/voice/VoiceWorkbench'
import WorkflowPage from '@/features/workflow/WorkflowPage'

const TOOL_TYPES: Record<string, ToolType> = {
  VoiceDesign: 'design',
  VoiceClone: 'clone',
  音频转换: 'convert',
}

export default function Home() {
  const { notice, notify } = useToast()
  const projects = useProjects(notify)
  const [page, setPage] = useState<Page>('声线')
  const { projectId, projectName } = projects

  const renderPage = () => {
    if (page === '系统状态') return <SystemPage />
    if (!projectId) return <p className="empty-state">{projects.projects.length === 0 ? '暂无项目，请先创建。' : '正在加载项目…'}</p>
    switch (page) {
      case '声线': return <WorkflowPage key={projectId} projectId={projectId} projectName={projectName} notify={notify} />
      case '配音': return <BatchPage key={projectId} projectId={projectId} notify={notify} />
      case '成品': return <ProductsPage key={projectId} projectId={projectId} projectName={projectName ?? '当前项目'} notify={notify} audioOnly />
      case '声线资产': return <AssetsPage key={projectId} projectId={projectId} notify={notify} />
      case '上传声线': return <UploadVoicePage key={projectId} projectId={projectId} notify={notify} />
      case '项目管理': return <ProjectsPage key={projectId} projectId={projectId} notify={notify} onChanged={projects.syncAfterChange} />
      default: return <ToolPage key={page} type={TOOL_TYPES[page] ?? 'convert'} notify={notify} />
    }
  }

  return <main className="app-shell">
    <Sidebar
      page={page}
      onNavigate={setPage}
      switcher={
        <ProjectSwitcher
          projects={projects.projects}
          projectId={projectId}
          onSwitchProject={projects.switchProject}
          onCreateProject={projects.createProject}
          onManage={() => setPage('项目管理')}
          notify={notify}
        />
      }
    />
    <section className="main-panel">
      <div className="top-status">
        {projectId ? `项目「${projectName ?? '…'}」已连接 FastAPI，模型任务在后台执行。` : '正在连接 FastAPI…'}
      </div>
      <div className="content-wrap">{renderPage()}</div>
    </section>
    <Toast notice={notice} />
  </main>
}
