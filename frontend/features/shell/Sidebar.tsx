'use client'

import { useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { Page } from '@/lib/nav'
import type { ProjectSummary } from '@/lib/types'

/** ===== 侧栏重排动画：只改这里就能控制移动效果 =====
 *  MOVE_MS   升到第一位那一行的滑动总时长（毫秒）
 *  FOLLOW_MS 被顶下去的其余行的滑动时长
 *  POP_SCALE 浮起那一行的最大放大倍数（1.04 = 放大 4%）
 */
const MOVE_MS = 520
const FOLLOW_MS = MOVE_MS * 0.8
const MOVE_EASING = 'cubic-bezier(.22,.68,.16,1)'
const POP_SCALE = 1.04
const POP_SHADOW = '0 12px 26px rgba(43, 42, 36, .22)'
const FLAT_SHADOW = '0 0 0 0 rgba(43, 42, 36, 0)'

type SidebarProps = {
  page: Page
  /** 全量项目：搜索跨整个项目库，不受历史栏条数限制。 */
  projects: ProjectSummary[]
  /** 历史项目栏：父级已裁成「最近 5 个」（进一个丢末尾一个），这里只负责渲染。 */
  recentProjects: ProjectSummary[]
  currentProjectId: string
  onNavigate: (page: Page) => void
  /** 唯一切换行为：切换当前项目并进入其工作台（落 last_step 由工作台自己恢复）。 */
  onOpenProject: (projectId: string) => void
  onNewProject: () => void
}

function ProjectRow({ project, active, current, onOpen, attach }: {
  project: ProjectSummary
  active: boolean
  current: boolean
  onOpen: (id: string) => void
  attach: (element: HTMLButtonElement | null) => void
}) {
  return <button
    ref={attach}
    className={`nav-link project-row${active ? ' active' : current ? ' current' : ''}`}
    title={project.name}
    onClick={() => onOpen(project.id)}
  >
    <span className="project-row-name">{project.name}</span>
  </button>
}

/** 新 IA 侧栏：项目管理（新建/搜索/按操作时间排序）→ 声线库 → 系统状态。无顶部切换器。 */
export function Sidebar({ page, projects, recentProjects, currentProjectId, onNavigate, onOpenProject, onNewProject }: SidebarProps) {
  const [query, setQuery] = useState('')
  const rowRefs = useRef(new Map<string, HTMLButtonElement>())
  const topsRef = useRef(new Map<string, number>())
  const orderRef = useRef<string[]>([])

  const searchResults = useMemo(() => {
    const keyword = query.trim().toLowerCase()
    if (!keyword) return null
    return projects.filter((project) => project.name.toLowerCase().includes(keyword))
  }, [projects, query])

  // 条数已由父级钉死（固定 5），这里不再为"让当前项目可见"扩窗口 ——
  // 当前项目在榜外时会被顶进第 1 位，末尾一条随之淘汰。
  const shown = searchResults ?? recentProjects

  // FLIP：本次提交后测得的 offsetTop 与上次提交记录的差值，就是这一行被列表重排推移的距离，
  // 先用 translateY 把它送回原位再放回到 0，视觉上就是"滑过去"而不是瞬间跳过去。
  // 升到第一位的那一行和被顶下去的那一行额外浮起（放大＋投影＋实底），落位后复原。
  useLayoutEffect(() => {
    const rows = shown
    const previousTops = topsRef.current
    const previousOrder = orderRef.current
    const nextOrder = rows.map((project) => project.id)
    const nextTops = new Map<string, number>()
    for (const project of rows) {
      const element = rowRefs.current.get(project.id)
      if (element) nextTops.set(project.id, element.offsetTop)
    }

    // 认顺序不认选中：仅仅换了当前项目、列表没真重排时不滑。
    const reordered = previousOrder.length > 0 && nextOrder.join('|') !== previousOrder.join('|')
    if (reordered && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      const floating = new Set([nextOrder[0], previousOrder[0]])
      for (const project of rows) {
        const element = rowRefs.current.get(project.id)
        const from = previousTops.get(project.id)
        if (!element || from === undefined) continue
        const delta = from - element.offsetTop
        if (Math.abs(delta) < 1) continue
        const raised = floating.has(project.id)
        element.animate([
          { transform: `translateY(${delta}px) scale(1)`, boxShadow: FLAT_SHADOW, backgroundColor: 'rgba(255,255,255,0)', zIndex: raised ? 3 : 1 },
          { transform: `translateY(${delta * .5}px) scale(${raised ? POP_SCALE : 1})`, boxShadow: raised ? POP_SHADOW : FLAT_SHADOW, backgroundColor: raised ? '#fff' : 'rgba(255,255,255,0)', zIndex: raised ? 3 : 1, offset: .45 },
          { transform: 'translateY(0) scale(1)', boxShadow: FLAT_SHADOW, backgroundColor: 'rgba(255,255,255,0)', zIndex: raised ? 3 : 1 },
        ], { duration: raised ? MOVE_MS : FOLLOW_MS, easing: MOVE_EASING })
      }
    }

    topsRef.current = nextTops
    orderRef.current = nextOrder
  })

  const attachRow = (id: string) => (element: HTMLButtonElement | null) => {
    if (element) rowRefs.current.set(id, element)
    else rowRefs.current.delete(id)
  }

  // 实心点只代表"当前打开的页面"：项目行只在工作台时算选中，
  // 其它页面（总览/声线库）下当前项目改用弱高亮，避免两个亮点并存。
  const rowState = (id: string) => ({
    active: page === '工作台' && id === currentProjectId,
    current: id === currentProjectId,
  })

  return <aside className="sidebar">
    <div className="nav-group">
      <h3>项目管理</h3>
      <button className="nav-link new-project" onClick={onNewProject}>＋ 新建项目</button>
      <input
        className="project-search"
        value={query}
        onChange={(event) => setQuery(event.target.value)}
        placeholder="搜索项目…"
      />
      {shown.map((project) => {
        const state = rowState(project.id)
        return <ProjectRow
          key={project.id}
          project={project}
          active={state.active}
          current={state.current}
          onOpen={onOpenProject}
          attach={attachRow(project.id)}
        />
      })}
      {searchResults && searchResults.length === 0 && <p className="hint">无匹配项目</p>}
      <button className={page === '项目总览' ? 'nav-link manage-link active' : 'nav-link manage-link'} onClick={() => onNavigate('项目总览')}>项目统计与删除</button>
    </div>

    <div className="nav-group">
      <h3>声线库</h3>
      <button className={page === '全部声线' ? 'nav-link active' : 'nav-link'} onClick={() => onNavigate('全部声线')}>全部声线</button>
      <button className={page === '上传声线' ? 'nav-link active' : 'nav-link'} onClick={() => onNavigate('上传声线')}>上传声线</button>
    </div>

    <div className="nav-group">
      <h3>系统</h3>
      <button className={page === '系统状态' ? 'nav-link active' : 'nav-link'} onClick={() => onNavigate('系统状态')}>系统状态</button>
    </div>

    <div className="sidebar-footer">当前页面：{page}</div>
  </aside>
}
