'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { apiDelete, apiGet, apiPost, apiPut } from '@/lib/api'
import type { Notify, ProjectSummary } from '@/lib/types'
import { Button, DataTable, Modal, RowMenu, formatTime } from '@/components/ui'
import type { RowMenuItem } from '@/components/ui'

type SortKey = 'activity' | 'name' | 'progress'

type ProjectsPageProps = {
  projectId: string
  notify: Notify
  /** 删除/改名后同步全局项目列表；带 id 时同时切换当前项目。 */
  onChanged: (activatedId?: string) => void
  /** 「进入」= 切换项目并落在其 last_step 工作台。 */
  onEnter: (id: string) => void
  onNewProject: () => void
}

/** 首页 = 项目总览：无标题文案，统计来自实时数据库；行悬停 [···] 菜单承载重命名/删除（方式在弹窗里选）。 */
export default function ProjectsPage({ projectId, notify, onChanged, onEnter, onNewProject }: ProjectsPageProps) {
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState<SortKey>('activity')
  const [showArchived, setShowArchived] = useState(false)
  const [busy, setBusy] = useState(false)
  const [renameTarget, setRenameTarget] = useState<ProjectSummary | null>(null)
  const [renameValue, setRenameValue] = useState('')
  const [deleteTarget, setDeleteTarget] = useState<ProjectSummary | null>(null)
  const [deleteMode, setDeleteMode] = useState<'archive' | 'purge'>('archive')
  const [confirmName, setConfirmName] = useState('')
  const [loading, setLoading] = useState(true)

  // force：总览页的「刷新」按钮必须真的回源，否则 5 秒内点它会命中内存缓存、
  // 拿到一模一样的列表，看上去就是"按钮没反应"。
  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setProjects(await apiGet<ProjectSummary[]>(`/api/projects?include_archived=${showArchived ? 'true' : 'false'}`, { force: true }))
    } finally {
      setLoading(false)
    }
  }, [showArchived])

  useEffect(() => {
    refresh().catch((error: Error) => notify(error.message))
  }, [projectId, showArchived, refresh])

  const visible = useMemo(() => {
    const keyword = query.trim().toLowerCase()
    const filtered = keyword ? projects.filter((project) => project.name.toLowerCase().includes(keyword)) : projects
    const sorted = [...filtered]
    if (sort === 'name') sorted.sort((a, b) => a.name.localeCompare(b.name, 'zh'))
    if (sort === 'progress') sorted.sort((a, b) =>
      (b.completed_count / Math.max(b.task_count, 1)) - (a.completed_count / Math.max(a.task_count, 1)))
    // activity 顺序由后端排好（last_activity DESC）
    return sorted
  }, [projects, query, sort])

  const doRename = async () => {
    if (!renameTarget) return
    const name = renameValue.trim()
    if (!name) { notify('项目名称不能为空'); return }
    setBusy(true)
    try {
      await apiPut(`/api/projects/${renameTarget.id}`, { name })
      setRenameTarget(null)
      await refresh()
      onChanged(renameTarget.id === projectId ? renameTarget.id : undefined)
      notify(`项目已重命名为「${name}」`)
    } catch (error) { notify((error as Error).message) } finally { setBusy(false) }
  }

  /** 菜单只有一个「删除」入口，具体方式在弹窗里选。
   *  已删除的项目没法再"保留声线删除"（它已经归档了），所以直接落在彻底删除上。 */
  const openDelete = (project: ProjectSummary) => {
    setDeleteTarget(project)
    setDeleteMode(project.status === 'deleted' ? 'purge' : 'archive')
    setConfirmName('')
  }

  const doDelete = async () => {
    if (!deleteTarget) return
    if (deleteMode === 'purge' && confirmName !== deleteTarget.name) {
      notify('彻底删除需输入项目完整名称以确认')
      return
    }
    setBusy(true)
    try {
      const result = await apiDelete<{ deleted: string; retained_voices: string[] }>(
        `/api/projects/${deleteTarget.id}?mode=${deleteMode}`,
      )
      setDeleteTarget(null)
      await refresh()
      onChanged()
      notify(deleteMode === 'archive'
        ? `项目「${result.deleted}」已删除，声线继续保留`
        : `项目「${result.deleted}」已彻底删除${result.retained_voices.length ? `；其他项目仍在使用的声线已保留：${result.retained_voices.join('、')}` : ''}`)
    } catch (error) { notify((error as Error).message) } finally { setBusy(false) }
  }

  const actionsFor = (project: ProjectSummary) => {
    const archived = project.status === 'deleted'
    // 删除只有一颗入口，点开后再选「保留声线」还是「彻底」。
    // 已删除的项目只剩彻底删除这一种方式，文案直接说清，不让人点进去发现没得选。
    const items: RowMenuItem[] = archived
      ? [{ label: '彻底删除', danger: true, onClick: () => openDelete(project) }]
      : [
        { label: '重命名', onClick: () => { setRenameTarget(project); setRenameValue(project.name) } },
        { label: '删除…', danger: true, onClick: () => openDelete(project) },
      ]
    return <span className="row-actions">
      {project.id === projectId && !archived
        ? <span className="hint">当前项目</span>
        : archived
          ? <span className="hint">已删除</span>
          : <Button small onClick={() => onEnter(project.id)}>进入</Button>}
      <RowMenu items={items} />
    </span>
  }

  return <>
    <div className="projects-toolbar">
      <Button primary onClick={onNewProject}>＋ 新建项目</Button>
      <input className="projects-search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索项目名称…" />
      <select value={sort} onChange={(event) => setSort(event.target.value as SortKey)} className="projects-sort">
        <option value="activity">按最近活动</option>
        <option value="name">按名称</option>
        <option value="progress">按完成进度</option>
      </select>
      <label className="projects-archived">
        <input type="checkbox" checked={showArchived} onChange={(event) => setShowArchived(event.target.checked)} />
        查看已删除项目
      </label>
      <Button onClick={() => refresh().catch((error: Error) => notify(error.message))} disabled={loading}>
        {loading ? '刷新中…' : '刷新'}
      </Button>
    </div>
    {loading && projects.length === 0
      ? <p className="empty-state">正在加载项目列表…</p>
      : <DataTable
        headers={['当前', '项目名称', '声线槽位', '固化声线', '单句成品', '批量任务', '已完成', '最近活动', '项目ID', '操作']}
        rowKeys={visible.map((project) => project.id)}
        rowClasses={visible.map((project) => project.id === projectId ? 'row-current' : project.status === 'deleted' ? 'row-archived' : undefined)}
        rows={visible.map((project) => [
          project.id === projectId ? '✓' : '',
          <span className="project-name-cell" key={project.id} title={project.name}>
            {project.name}
          </span>,
          String(project.slot_count),
          String(project.voice_count),
          String(project.single_count),
          String(project.task_count),
          String(project.completed_count),
          formatTime(project.last_activity),
          project.id,
          actionsFor(project),
        ])}
      />}

    {renameTarget && <Modal
      title={`重命名项目「${renameTarget.name}」`}
      onClose={() => setRenameTarget(null)}
      actions={<><Button onClick={() => setRenameTarget(null)}>取消</Button><Button primary disabled={busy} onClick={doRename}>保存</Button></>}
    >
      <input value={renameValue} onChange={(event) => setRenameValue(event.target.value)} placeholder="新的项目名称" />
    </Modal>}

    {deleteTarget && <Modal
      title={deleteTarget.status === 'deleted'
        ? `彻底删除项目「${deleteTarget.name}」`
        : `删除项目「${deleteTarget.name}」`}
      onClose={() => setDeleteTarget(null)}
      actions={<>
        <Button onClick={() => setDeleteTarget(null)}>取消</Button>
        <Button danger={deleteMode === 'purge'} primary={deleteMode !== 'purge'} disabled={busy} onClick={doDelete}>
          {deleteMode === 'archive' ? '确认删除（保留声线）' : '确认彻底删除'}
        </Button>
      </>}
    >
      {/* 归档过的项目没有"再归档一次"这回事，只留彻底删除这一种方式。 */}
      {deleteTarget.status !== 'deleted' && <div className="delete-option">
        <label><input type="radio" name="delete-mode" checked={deleteMode === 'archive'} onChange={() => setDeleteMode('archive')} /> 删除项目，保留声线</label>
        <p className="hint">项目从列表消失；声线、清单关系与音频文件全部保留，随时可另建项目复用。</p>
      </div>}
      {/* 确认输入框常驻，未选中该模式时压暗并禁用：原来它随单选钮条件渲染，
          点一下「彻底删除」弹窗就当场长高一大截、点回去又缩短。 */}
      <div className={deleteMode === 'purge' ? 'delete-option' : 'delete-option is-off'}>
        <label><input type="radio" name="delete-mode" checked={deleteMode === 'purge'} onChange={() => setDeleteMode('purge')} /> 彻底删除</label>
        <p className="hint">删除项目数据与文件；仍被其他项目使用的声线会自动保留并列在结果里。</p>
        <p>输入项目完整名称以确认：<span className="confirm-name">{deleteTarget.name}</span></p>
        <input value={confirmName} disabled={deleteMode !== 'purge'}
          onChange={(event) => setConfirmName(event.target.value)} placeholder="完整项目名称" />
      </div>
      <p className="hint">
        {deleteTarget.status === 'deleted'
          ? '删除前会自动创建完整备份快照。'
          : '两种方式都会在删除前自动创建完整备份快照。'}
      </p>
    </Modal>}
  </>
}
