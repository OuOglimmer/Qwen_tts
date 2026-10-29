'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { apiGet, apiPost, onProjectWrite } from '@/lib/api'
import type { ProjectSummary, Notify } from '@/lib/types'

/** 侧栏「历史项目」栏固定条数：进一个，末尾淘汰一个。多出来的靠搜索或总览页。 */
export const RECENT_LIMIT = 5

/**
 * 项目域的全局状态：列表、当前项目、创建/切换/删除后的同步。
 * 组合根（app/page.tsx）持有唯一实例，页面通过回调影响它，不各自持有一份。
 */
/** 向导里勾选的声线：只建清单关联，不复制音频。 */
export type ProjectVoicePick = { voiceId: string; displayName: string }

export function useProjects(notify: Notify) {
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [projectId, setProjectId] = useState('')
  /** 历史项目栏的提升队列（最近在前）：被操作过、或从榜外点进来的项目都排在最前。 */
  const [recent, setRecent] = useState<string[]>([])
  // 本会话内刚由向导建出的项目 id；用于「新建项目进第 1 步、其余进第 2 步」的判定。
  // 用 ref 而非 state：进入后清空它不应触发整树重渲染（避免切项目时的卡顿）。
  const recentlyCreatedRef = useRef('')
  // 新建项目的目标步骤（方式 A→1 / 方式 B→2），组合根消费后复位。
  const entryStepRef = useRef<1 | 2>(2)
  const projectIdRef = useRef('')

  // 不带项目 id 的写请求（步骤 1 的 voice-requests）要算到当前项目头上，监听器只挂载一次，
  // 所以用 ref 读最新的当前项目，而不是把 projectId 放进依赖里反复重订阅。
  useEffect(() => { projectIdRef.current = projectId }, [projectId])

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

  /** 顶到历史项目栏第一位（已在第一位时保持原数组，避免无谓重渲染）。 */
  const promote = useCallback((id: string) => {
    setRecent((current) => current[0] === id ? current : [id, ...current.filter((previous) => previous !== id)])
  }, [])

  // 历史项目栏的完整顺序：提升过的（最近一次在最前）→ 其余保持后端按活动时间的排序。
  // 栏位只取前 RECENT_LIMIT 条，所以这里算全量、裁剪交给 sidebarProjects。
  const orderedProjects = useMemo(() => {
    const promoted = recent
      .map((id) => projects.find((project) => project.id === id))
      .filter((project): project is ProjectSummary => project !== undefined)
    const promotedIds = new Set(promoted.map((project) => project.id))
    return [...promoted, ...projects.filter((project) => !promotedIds.has(project.id))]
  }, [projects, recent])

  /**
   * 当前项目落在历史项目栏之外（从总览页点进老项目、或重命名后切过去）时，
   * 把它顶进第 1 位、末尾那条淘汰，栏位恒为 5 条。
   * 点在栏内的不动顺序 —— 否则每点一次，列表就在鼠标底下换位置。
   */
  const revealIfHidden = useCallback((id: string) => {
    if (orderedProjects.findIndex((project) => project.id === id) >= RECENT_LIMIT) promote(id)
  }, [orderedProjects, promote])

  const switchProject = useCallback(async (id: string) => {
    setProjectId(id)
    revealIfHidden(id)
    try { await apiPost(`/api/projects/${id}/activate`) } catch { /* 保持本地选中，持久化失败不阻塞 */ }
  }, [revealIfHidden])

  // 只有"真的改了项目数据"（写请求成功）才把它顶到侧栏第一位；
  // 选中、翻步骤、纯浏览都不排序，判定规则见 lib/api 的 onProjectWrite。
  useEffect(() => onProjectWrite((writtenId) => {
    const id = writtenId ?? projectIdRef.current
    if (id) promote(id)
  }), [promote])

  /**
   * 新建项目的唯一入口：建项目 → （可选）把选中的声线挂进清单 → 刷新列表并选中。
   *
   * 必须由这里统一创建，不能让调用方各自 apiPost('/api/projects')：只有本函数会
   * 置位 recentlyCreatedRef，组合根据此决定落点（方式 A→第1步 / 方式 B→第2步）。
   * 挂载声线失败不丢弃已建好的项目，把失败明细回传给调用方提示。
   */
  const createProject = useCallback(async (name: string, picks: ProjectVoicePick[] = []) => {
    const project = await apiPost<{ id: string; name: string }>('/api/projects', { name: name.trim() })
    const failed: string[] = []
    for (const pick of picks) {
      try {
        await apiPost(`/api/projects/${project.id}/voices/attach`, {
          voice_id: pick.voiceId,
          display_name: pick.displayName,
        })
      } catch (error) {
        failed.push(`${pick.displayName}（${(error as Error).message}）`)
      }
    }
    setProjectId(project.id)
    recentlyCreatedRef.current = project.id
    // 落点随创建方式而定：带声线（方式 B）已可直接配音 → 第 2 步；
    // 不带声线（方式 A）要先造声线 → 第 1 步。
    entryStepRef.current = picks.length > 0 ? 2 : 1
    // force 绕过 GET 缓存：否则 5 秒内新建的项目可能因命中旧列表而"没出现"。
    const list = await apiGet<ProjectSummary[]>('/api/projects', { force: true })
    setProjects(list)
    return { ...project, failed }
  }, [])

  /**
   * 组合根在 openProject 时调用，取出本次进入应落在第几步并复位。
   * 新建项目用创建时定下的落点（方式 A→1 / 方式 B→2），其余既有项目一律第 2 步。
   */
  const consumeEntryStep = useCallback((id: string) => {
    if (recentlyCreatedRef.current === id) {
      recentlyCreatedRef.current = ''
      return entryStepRef.current
    }
    return 2 as const
  }, [])

  // 项目管理页切换/新建成功后带来的新当前项目；缺省时仅在
  // 当前项目已不存在（被删除）时回退到列表第一项。
  const syncAfterChange = useCallback(async (activatedId?: string) => {
    try {
      // force：删除/重命名刚改过数据，命中 5 秒旧缓存会让列表看起来"没反应"。
      const list = await apiGet<ProjectSummary[]>('/api/projects', { force: true })
      setProjects(list)
      if (activatedId && list.some((project) => project.id === activatedId)) {
        setProjectId(activatedId)
        revealIfHidden(activatedId)
        return
      }
      if (!list.find((project) => project.id === projectId)) {
        const next = list[0]
        if (next) { setProjectId(next.id); await apiPost(`/api/projects/${next.id}/activate`).catch(() => undefined) }
        else setProjectId('')
      }
    } catch (error) { notify((error as Error).message) }
  }, [projectId, notify, revealIfHidden])

  const projectName = projects.find((project) => project.id === projectId)?.name

  // 历史项目栏：固定 RECENT_LIMIT 条，绝不为"让当前项目可见"临时放宽 —— 当前项目
  // 若在榜外，switchProject 已经把它顶进第 1 位，末尾那条自然被淘汰。
  const sidebarProjects = useMemo(() => orderedProjects.slice(0, RECENT_LIMIT), [orderedProjects])

  return { projects, sidebarProjects, projectId, projectName, consumeEntryStep, refreshProjects, switchProject, createProject, syncAfterChange }
}
