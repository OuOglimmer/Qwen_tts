'use client'

import { useCallback, useEffect, useState } from 'react'
import { apiDelete, apiGet, apiPost } from '@/lib/api'
import type { Notify, ProjectSummary } from '@/lib/types'
import { Button, DataTable, Label, SectionTitle, formatTime, useConfirmDialog } from '@/components/ui'

type ProjectsPageProps = {
  projectId: string
  notify: Notify
  /** 新建/切换成功时带回新项目 id；删除时不带，由全局状态做回退。 */
  onChanged: (activatedId?: string) => void
}

export default function ProjectPage({ projectId, notify, onChanged }: ProjectsPageProps) {
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [newName, setNewName] = useState('')
  const [confirmName, setConfirmName] = useState('')
  const [busy, setBusy] = useState(false)
  const { confirm, dialogElement } = useConfirmDialog()

  const refresh = useCallback(async () => {
    setProjects(await apiGet<ProjectSummary[]>('/api/projects'))
  }, [])

  useEffect(() => { refresh().catch((error: Error) => notify(error.message)) }, [projectId])

  const create = async () => {
    if (!newName.trim()) { notify('请填写新项目名称'); return }
    setBusy(true)
    try {
      // 后端对重名项目会直接返回已有项目，无论哪种情况都把当前项目切过去。
      const project = await apiPost<{ id: string; name: string }>('/api/projects', { name: newName.trim() })
      setNewName(''); await refresh(); onChanged(project.id); notify(`项目「${project.name}」已创建并切换`)
    } catch (error) { notify((error as Error).message) } finally { setBusy(false) }
  }

  const switchTo = async (project: ProjectSummary) => {
    try {
      await apiPost(`/api/projects/${project.id}/activate`)
      // 关键：把新激活的项目 id 回传给全局状态，否则整站仍停留在原项目。
      onChanged(project.id); notify(`已切换到「${project.name}」`)
    } catch (error) { notify((error as Error).message) }
  }

  const remove = async () => {
    const current = projects.find((project) => project.id === projectId)
    if (!current) return
    if (confirmName !== current.name) { notify('请输入当前项目的完整名称以确认删除'); return }
    if (!(await confirm({
      title: '永久删除项目',
      message: `永久删除项目「${current.name}」及其全部声线、任务和音频？此操作前会自动创建备份快照。`,
      confirmText: '永久删除',
      danger: true,
    }))) return
    setBusy(true)
    try {
      await apiDelete(`/api/projects/${projectId}`)
      setConfirmName(''); onChanged(); notify(`项目「${current.name}」已删除`)
    } catch (error) { notify((error as Error).message) } finally { setBusy(false) }
  }

  return <>
    <SectionTitle title="项目管理" description="创建、切换和删除项目；统计列来自实时数据库，删除前会自动创建完整备份快照。" />
    <div className="button-row project-create">
      <div>
        <Label>新项目名称</Label>
        <input value={newName} onChange={(event) => setNewName(event.target.value)} placeholder="例如：有声书第一章" />
      </div>
      <Button primary disabled={busy} onClick={create}>新建项目</Button>
      <Button onClick={() => refresh()}>刷新项目列表</Button>
    </div>
    <DataTable
      headers={['当前', '项目名称', '声线槽位', '固化声线', '单句成品', '批量任务', '已完成', '最近活动', '短 ID', '操作']}
      rowClasses={projects.map((project) => (project.id === projectId ? 'row-current' : undefined))}
      rows={projects.map((project) => [
        project.id === projectId ? '✓' : '',
        project.name,
        String(project.slot_count),
        String(project.voice_count),
        String(project.single_count),
        String(project.task_count),
        String(project.completed_count),
        formatTime(project.last_activity),
        project.id.slice(0, 8),
        project.id === projectId ? <span className="hint">当前项目</span> : <Button small onClick={() => switchTo(project)}>切换</Button>,
      ])}
    />
    <div className="section-heading">危险操作</div>
    <Label>输入当前项目名称以确认删除</Label>
    <div className="button-row project-create">
      <div>
        <input value={confirmName} onChange={(event) => setConfirmName(event.target.value)} placeholder="完整项目名称" />
      </div>
      <Button disabled={busy || confirmName !== (projects.find((project) => project.id === projectId)?.name ?? '')} onClick={remove}>永久删除当前项目</Button>
    </div>
    {dialogElement}
  </>
}
