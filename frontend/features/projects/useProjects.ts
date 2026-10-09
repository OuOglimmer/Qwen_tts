'use client'

import { useCallback, useEffect, useState } from 'react'
import { apiGet, apiPost } from '@/lib/api'
import type { ProjectSummary, Notify } from '@/lib/types'

/**
 * 项目域的全局状态：列表、当前项目、创建/切换/删除后的同步。
 * 组合根（app/page.tsx）持有唯一实例，页面通过回调影响它，不各自持有一份。
 */
export function useProjects(notify: Notify) {
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [projectId, setProjectId] = useState('')

  const refreshProjects = useCallback(async () => {
    const list = await apiGet<ProjectSummary[]>('/api/projects')
    setProjects(list)
    return list
  }, [])

  // 首次进入：拉取列表并选中第一个项目（本地选中优先，激活请求不阻塞）。
  // notify/refreshProjects 恒定，仅挂载执行一次。
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    refreshProjects()
      .then((list) => { if (list[0]) setProjectId((current) => current || list[0].id) })
      .catch((error: Error) => notify(error.message))
  }, [])

  const switchProject = useCallback(async (id: string) => {
    setProjectId(id)
    try { await apiPost(`/api/projects/${id}/activate`) } catch { /* 保持本地选中，持久化失败不阻塞 */ }
  }, [])

  const createProject = useCallback(async (name: string) => {
    const project = await apiPost<{ id: string; name: string }>('/api/projects', { name })
    setProjectId(project.id)
    await refreshProjects()
    return project
  }, [refreshProjects])

  // 项目管理页切换/新建成功后带来的新当前项目；缺省时仅在
  // 当前项目已不存在（被删除）时回退到列表第一项。
  const syncAfterChange = useCallback(async (activatedId?: string) => {
    try {
      const list = await refreshProjects()
      if (activatedId && list.some((project) => project.id === activatedId)) {
        setProjectId(activatedId)
        return
      }
      if (!list.find((project) => project.id === projectId)) {
        const next = list[0]
        if (next) { setProjectId(next.id); await apiPost(`/api/projects/${next.id}/activate`).catch(() => undefined) }
        else setProjectId('')
      }
    } catch (error) { notify((error as Error).message) }
  }, [projectId, refreshProjects, notify])

  const projectName = projects.find((project) => project.id === projectId)?.name

  return { projects, projectId, projectName, refreshProjects, switchProject, createProject, syncAfterChange }
}
