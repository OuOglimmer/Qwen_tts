'use client'

import type { ReactNode } from 'react'
import type { Page } from '@/lib/nav'
import { NAV_GROUPS } from '@/lib/nav'

type SidebarProps = {
  page: Page
  onNavigate: (page: Page) => void
  /** 项目切换/新建区（ProjectSwitcher），由组合根注入，保持本组件无项目状态。 */
  switcher?: ReactNode
}

/** 纯导航侧栏：品牌、项目区插槽、导航组。 */
export function Sidebar({ page, onNavigate, switcher }: SidebarProps) {
  return <aside className="sidebar">
    <div className="brand">Qwen TTS Studio</div>
    {switcher}
    {NAV_GROUPS.map((group) => <div className="nav-group" key={group.title}>
      <h3>{group.title}</h3>
      {group.items.map((item) => (
        <button key={item} className={page === item ? 'nav-link active' : 'nav-link'} onClick={() => onNavigate(item)}>{item}</button>
      ))}
    </div>)}
    <div className="sidebar-footer">当前页面：{page}</div>
  </aside>
}
