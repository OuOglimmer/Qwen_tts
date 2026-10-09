'use client'

import { useState } from 'react'
import { Button } from '@/components/ui'
import type { ProjectSummary, Notify } from '@/lib/types'

type ProjectSwitcherProps = {
  projects: ProjectSummary[]
  projectId: string
  onSwitchProject: (id: string) => void
  /** 创建成功返回项目摘要（供提示文案使用）；失败时抛错由这里统一提示。 */
  onCreateProject: (name: string) => Promise<{ id: string; name: string }>
  onManage: () => void
  notify: Notify
}

/** 侧栏的项目区：切换当前项目 + 快捷新建。 */
export function ProjectSwitcher({ projects, projectId, onSwitchProject, onCreateProject, onManage, notify }: ProjectSwitcherProps) {
  const [newName, setNewName] = useState('')
  const [creating, setCreating] = useState(false)

  const create = async () => {
    const name = newName.trim()
    if (!name) { notify('请填写新项目名称'); return }
    setCreating(true)
    try {
      const project = await onCreateProject(name)
      setNewName('')
      notify(`项目「${project.name}」已创建并切换`)
    } catch (error) { notify((error as Error).message) } finally { setCreating(false) }
  }

  return <div className="current-project">
    <span>当前项目</span>
    <select value={projectId} onChange={(event) => onSwitchProject(event.target.value)} className="project-switch">
      {projects.length === 0 && <option value="">（无项目）</option>}
      {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
    </select>
    <div className="project-quick-row">
      <input
        value={newName}
        onChange={(event) => setNewName(event.target.value)}
        onKeyDown={(event) => { if (event.key === 'Enter') create() }}
        placeholder="新项目名称"
      />
      <Button primary disabled={creating} onClick={create}>创建</Button>
    </div>
    <button className="manage-link" onClick={onManage}>⚙ 统计与删除 → 项目管理</button>
  </div>
}
