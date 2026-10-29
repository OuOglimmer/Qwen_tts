'use client'

/**
 * 组合根（唯一有全局状态的组件）：
 * - 导航状态（当前页）留在这里；首页 = 项目总览。
 * - 项目域状态收敛到 useProjects；轻提示收敛到 useToast。
 * - 切换项目只有一个行为（switchProject+进入工作台），入口可以有多个：
 *   侧栏项目名、总览页「进入」、新建向导完成，都走 openProject。
 * key={projectId}：切换项目时强制重挂载页面，页面内状态随之复位。
 */
import { useRef, useState, useEffect, type ReactNode } from 'react'
import { Toast } from '@/components/ui'
import { useToast } from '@/lib/hooks'
import { type Page } from '@/lib/nav'
import { AssetsPage } from '@/features/assets/AssetsPage'
import { UploadVoicePage } from '@/features/assets/UploadVoicePage'
import { NewProjectWizard } from '@/features/projects/NewProjectWizard'
import { useProjects } from '@/features/projects/useProjects'
import ProjectsPage from '@/features/projects/ProjectsPage'
import { Sidebar } from '@/features/shell/Sidebar'
import SystemPage from '@/features/system/SystemPage'
import { STEPS, type Step } from '@/features/workflow/steps'
import { useWorkflowStep } from '@/features/workflow/useWorkflowStep'
import WorkflowPage from '@/features/workflow/WorkflowPage'

export default function Home() {
  const { notice, notify } = useToast()
  const projects = useProjects(notify)
  const [page, setPage] = useState<Page>('项目总览')
  const [wizardOpen, setWizardOpen] = useState(false)
  const { projectId, projectName, consumeEntryStep } = projects
  // 步骤条挂在 header 里，状态必须提升到组合根；仅「工作台」页且有项目时显示。
  // entryStep 是 state 而非 ref：切项目那一刻定下落点并触发重渲染，
  // useWorkflowStep 才能在 projectId 变化时用新落点复位（ref 改动不引发渲染，太脆弱）。
  const [entryStep, setEntryStep] = useState<Step>(2)
  const workflow = useWorkflowStep(projectId, entryStep)

  // 主题：初值跟随 <head> 里已设好的 data-theme（避免白闪），切换时同步写入 localStorage + 根节点
  const [theme, setTheme] = useState<'light' | 'dark'>('light')
  useEffect(() => {
    setTheme((document.documentElement.dataset.theme as 'light' | 'dark') || 'light')
  }, [])
  const toggleTheme = () => {
    const next = theme === 'light' ? 'dark' : 'light'
    document.documentElement.dataset.theme = next
    try { localStorage.setItem('theme', next) } catch { /* 隐私模式忽略 */ }
    setTheme(next)
  }

  // 已访问过的页面只挂载一次、之后常驻（keep-alive）：切走不卸载，只靠 hidden 切换显隐，
  // 因此第二次点回同一页面不再重新加载、状态与数据都还在。隐藏时的轮询/音频由各自逻辑暂停。
  const visitedRef = useRef<Set<Page>>(new Set<Page>(['项目总览']))
  const go = (next: Page) => {
    visitedRef.current.add(next)
    setPage(next)
  }
  const isActive = (key: Page) => page === key

  const openProject = (id: string) => {
    // 取回落点（方式 A→第1步 / 方式 B→第2步 / 其余→第2步）并写入 state，
    // 随 setProjectId 一起重渲染；不 await 激活请求，切页立即生效避免卡顿。
    setEntryStep(consumeEntryStep(id))
    projects.switchProject(id)
    go('工作台')
  }

  // 每个页面首次访问才挂载；之后一直挂在 DOM 里，靠 hidden 显隐。未访问的页面返回 null。
  const slot = (key: Page, node: ReactNode) =>
    visitedRef.current.has(key)
      ? <div key={key} className="page-slot" hidden={!isActive(key)}>{node}</div>
      : null

  return <main className="app-shell">
    <header className="app-header">
      <div className="brand-block">
        <span className="brand">TTS TOOLS</span>
      </div>
      {page === '工作台' && projectId && (
        <nav className="workflow-nav" aria-label="声线创作步骤">
          <span className="project-name" title={projectName}>{projectName && projectName.length > 4 ? projectName.slice(0, 4) + '…' : (projectName ?? '')}</span>
          <span className="nav-divider" />
          {STEPS.map((item) => (
            <button key={item.key} className={workflow.step === item.key ? 'active' : ''} onClick={() => workflow.changeStep(item.key)}>
              <span>{item.key}</span>{item.label}
            </button>
          ))}
        </nav>
      )}
      <div className="header-actions">
        <button type="button" className="theme-toggle" onClick={toggleTheme} aria-label="切换明暗主题" title={theme === 'light' ? '切换到黑暗模式' : '切换到明亮模式'}>
          {theme === 'light' ? (
            <svg className="theme-toggle-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8Z" />
            </svg>
          ) : (
            <svg className="theme-toggle-ico" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <circle cx="12" cy="12" r="4.2" />
              <path d="M12 2.5v2.2M12 19.3v2.2M4.6 4.6l1.6 1.6M17.8 17.8l1.6 1.6M2.5 12h2.2M19.3 12h2.2M4.6 19.4l1.6-1.6M17.8 6.2l1.6-1.6" />
            </svg>
          )}
        </button>
      </div>
    </header>
    <div className="app-body">
      <Sidebar
        page={page}
        projects={projects.projects}
        recentProjects={projects.sidebarProjects}
        currentProjectId={projectId}
        onNavigate={go}
        onOpenProject={openProject}
        onNewProject={() => setWizardOpen(true)}
      />
      <section className="main-panel">
        <div className="content-wrap">
          {slot('系统状态', <SystemPage />)}
          {slot('项目总览', <ProjectsPage
            key={projectId}
            projectId={projectId}
            notify={notify}
            onChanged={projects.syncAfterChange}
            onEnter={openProject}
            onNewProject={() => setWizardOpen(true)}
          />)}
          {slot('全部声线', <AssetsPage key={projectId || 'global'} projectId={projectId} notify={notify} />)}
          {slot('上传声线', <UploadVoicePage notify={notify} />)}
          {slot('工作台', projectId
            ? <WorkflowPage key={projectId} projectId={projectId} projectName={projectName} step={workflow.step} onStepChange={workflow.changeStep} notify={notify} active={isActive('工作台')} />
            : <p className="empty-state">{projects.projects.length === 0 ? '暂无项目，点击侧栏「＋ 新建项目」开始。' : '正在加载项目…'}</p>)}
        </div>
      </section>
    </div>
    {wizardOpen && <NewProjectWizard
      onClose={() => setWizardOpen(false)}
      onCreate={async (name, picks) => {
        // 创建（含声线挂载）全部交给 useProjects：只有它会置位"新建"标记，
        // openProject 才能据此把方式 A 落到第 1 步、方式 B 落到第 2 步。
        const project = await projects.createProject(name, picks)
        setWizardOpen(false)
        openProject(project.id)
        return project
      }}
      notify={notify}
    />}
    <Toast notice={notice} />
  </main>
}
