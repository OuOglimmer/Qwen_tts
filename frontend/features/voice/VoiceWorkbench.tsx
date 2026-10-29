'use client'

import { useEffect, useState } from 'react'
import { apiDelete, apiGet, apiPost, apiPut } from '@/lib/api'
import type { Candidate, Notify, VoiceGenTask, VoiceRequest } from '@/lib/types'
import { Button, SectionTitle, useConfirmDialog } from '@/components/ui'
import { readDraft, readSeenTask, writeSeenTask } from './draft'
import { BulkAddVoices } from './BulkAddVoices'
import { AttachLibraryVoices } from './AttachLibraryVoices'
import { CandidatePanel } from './CandidatePanel'
import { RequestTable } from './RequestTable'
import { VoiceHistoryDialog } from './VoiceHistoryDialog'
import { VoiceRequestForm } from './VoiceRequestForm'
import { useVoiceCandidates } from './useVoiceCandidates'
import { useVoiceDraft } from './useVoiceDraft'
import { useVoiceGeneration } from './useVoiceGeneration'
import { useVoiceRequests } from './useVoiceRequests'

/**
 * 「声线」工作台（流程第 1 步）：组合槽位数据、草稿、候选与生成任务四个领域，
 * 自身只做编排——状态各归其 hook，展示各归其子组件。
 */
export default function VoiceWorkbench({ projectId, notify, active, onNext }: { projectId: string; notify: Notify; active?: boolean; onNext?: () => void }) {
  const voiceRequests = useVoiceRequests(projectId)
  const [activeRequestId, setActiveRequestId] = useState<string | null>(null)
  const draft = useVoiceDraft(projectId, activeRequestId)
  const candidates = useVoiceCandidates()
  const generation = useVoiceGeneration({
    projectId,
    notify,
    active: active ?? true,
    activeRequestId,
    adoptRequest: setActiveRequestId,
    loadCandidates: candidates.loadCandidates,
    loadRequests: voiceRequests.loadRequests,
  })
  const [fixing, setFixing] = useState(false)
  const [bulkOpen, setBulkOpen] = useState(false)
  const [libOpen, setLibOpen] = useState(false)
  // 查看历史声线的目标槽位：null = 弹窗关着。
  const [historyTarget, setHistoryTarget] = useState<VoiceRequest | null>(null)
  const { confirm, dialogElement } = useConfirmDialog()

  const activeRequest = voiceRequests.requests.find((request) => request.id === activeRequestId) ?? null
  // 固化按钮只作用当前槽位：activeRequest.status === 'selected' 即"当前槽位已选定候选"。
  // 真正的"是否有候选 selected=1"拦截在后端（selected_requests JOIN candidates）。
  const canFix = activeRequest?.status === 'selected'

  // 进入页面时恢复上次状态：优先接管仍在进行的生成任务，其次恢复草稿记录的槽位。
  // 字段草稿由 useVoiceDraft 自行恢复，这里只决定接管哪个槽位。
  useEffect(() => {
    let cancelled = false
    const restore = async () => {
      const saved = readDraft(projectId)
      const [rows, task] = await Promise.all([
        voiceRequests.fetchRequests(),
        apiGet<VoiceGenTask>('/api/voice-generation/status'),
      ])
      if (cancelled) return
      voiceRequests.setRequests(rows)
      voiceRequests.loadProgress().catch(() => undefined)
      let targetId: string | null = null
      if (saved?.activeRequestId && rows.some((item) => item.id === saved.activeRequestId)) {
        targetId = saved.activeRequestId
      }
      let announce = ''
      if (task && task.project_id === projectId) {
        generation.observe(task)
        if (!task.finished) {
          setActiveRequestId(task.request_id)
          await candidates.loadCandidates(task.request_id)
          return
        }
        const unseen = readSeenTask() !== task.request_id
        writeSeenTask(task.request_id)
        // 离开页面期间才完成的生成：恢复其槽位与候选并补一次完成提示。
        if (!targetId && unseen && rows.some((item) => item.id === task.request_id)) {
          targetId = task.request_id
          announce = task.error
            ? `候选生成异常：${task.error}`
            : task.cancelled
              ? `已取消：本轮完成 ${task.generated}/${task.total} 个候选`
              : `已生成 ${task.generated} 个候选`
        }
      }
      if (targetId) {
        setActiveRequestId(targetId)
        await candidates.loadCandidates(targetId)
      }
      if (announce && !cancelled) notify(announce)
    }
    restore().catch((error: Error) => { if (!cancelled) notify(error.message) })
    return () => { cancelled = true }
    // key={projectId} 保证换项目时整页重挂载，这里只需在挂载时执行一次。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  const loadRequest = (request: VoiceRequest) => {
    setActiveRequestId(request.id)
    draft.loadFromRequest(request)
    candidates.loadCandidates(request.id).catch((error: Error) => notify(error.message))
  }

  const handleSelectSlot = (requestId: string | null) => {
    const request = requestId ? voiceRequests.requests.find((item) => item.id === requestId) : null
    if (request) loadRequest(request)
    else { setActiveRequestId(null); candidates.clear(); draft.resetForNewVoice() }
  }

  const handleGenerate = async () => {
    if (!draft.name.trim() || !draft.prompt.trim() || !draft.auditionText.trim()) { notify('声线名称、提示词和试听文本均为必填项'); return }
    const task = await generation.generate({
      name: draft.name, prompt: draft.prompt, auditionText: draft.auditionText, language: draft.language, count: draft.count, requestId: activeRequestId,
    })
    if (task) {
      setActiveRequestId(task.request_id)
      try { await candidates.loadCandidates(task.request_id) } catch (error) { notify((error as Error).message) }
    }
  }

  const saveDraft = async () => {
    if (!activeRequestId) { notify('请先从工作表加载一个声线槽位'); return }
    try {
      await apiPut(`/api/voice-requests/${activeRequestId}`, {
        project_id: projectId, name: draft.name, prompt: draft.prompt, audition_text: draft.auditionText, language: draft.language,
      })
      await voiceRequests.loadRequests()
      notify('草稿已保存')
    } catch (error) { notify((error as Error).message) }
  }

  const addVoices = async (names: string[], language: string) => {
    try {
      const created = await voiceRequests.createSlots(names, language)
      setBulkOpen(false)
      notify(`已添加 ${created.length} 个声线槽位：${created.join('、')}`)
    } catch (error) { notify((error as Error).message) }
  }

  /** 从全局声线库把已有声线挂成这个项目的已固化槽位（第 1 步就能看见）。 */
  const attachFromLibrary = async (voiceIds: string[]) => {
    try {
      const result = await apiPost<{ created: { name: string }[] }>(`/api/projects/${projectId}/voice-requests/attach-library`, { voice_ids: voiceIds })
      setLibOpen(false)
      await voiceRequests.loadRequests()
      notify(`已从声线库添加 ${result.created.length} 条声线：${result.created.map((item) => item.name).join('、')}`)
    } catch (error) { notify((error as Error).message) }
  }

  const renameRequest = async (request: VoiceRequest, name: string) => {
    try {
      // PUT 整行覆盖设定，改名必须带上原有提示词与试听文本，否则会把它们清空。
      await apiPut(`/api/voice-requests/${request.id}`, {
        project_id: projectId, name, prompt: request.prompt, audition_text: request.audition_text, language: request.language,
      })
      if (activeRequestId === request.id) draft.setName(name)
      await voiceRequests.loadRequests()
      notify(`已改名为「${name}」`)
    } catch (error) { notify((error as Error).message) }
  }

  /** 候选卡上的「选定」是个开关：已选定的再点一次就取消，不必换选另一个来覆盖。 */
  const select = async (candidateId: string, selected: boolean) => {
    // 没有激活槽位时静默 return 会让按钮"点了没反应"，统一给一条可读提示。
    if (!activeRequestId) { notify('请先在左侧选择一个声线槽位'); return }
    try {
      if (selected) await apiPost(`/api/voice-requests/${activeRequestId}/select/${candidateId}`)
      else await apiPost(`/api/voice-requests/${activeRequestId}/deselect`)
      await candidates.loadCandidates(activeRequestId)
      await voiceRequests.loadRequests()
      notify(selected
        ? '已选定该候选，可继续固化'
        : '已取消选定，当前槽位不再参与固化')
    } catch (error) { notify((error as Error).message) }
  }

  const toggleShortlist = async (candidate: Candidate) => {
    if (!activeRequestId) { notify('请先在左侧选择一个声线槽位'); return }
    const next = !candidate.shortlisted
    try {
      await apiPost(`/api/voice-requests/${activeRequestId}/candidates/${candidate.id}/shortlist`, { shortlisted: next })
      await candidates.loadCandidates(activeRequestId)
      await voiceRequests.loadRequests()
      // 暂存/移出的结果体现在卡片标签和下方暂存区两处，主动提示一次免得用户以为没生效。
      notify(next ? '已暂存，可在下方「暂存对比」里跨轮比较' : '已移出暂存')
    } catch (error) { notify((error as Error).message) }
  }

  const keepSingle = async (candidateId: string) => {
    if (!activeRequestId) { notify('请先在左侧选择一个声线槽位'); return }
    try {
      await apiPost<{ output_path: string; url: string | null }>(`/api/voice-requests/${activeRequestId}/candidates/${candidateId}/keep-single`)
      await voiceRequests.loadRequests(); notify('已保存为单句成品，可在「成品导出」里查看')
    } catch (error) { notify((error as Error).message) }
  }

  const fix = async () => {
    if (!activeRequestId) { notify('请先在左侧选择一个声线槽位'); return }
    // 「已固化过、这次要覆盖」的准确信号：重新选定候选后 status 会退回 selected，
    // 但 voice_id 仍指向当前在用的那把声线（select_candidate 不清它）。
    // 这一步会把旧资产退役成 replaced —— 界面上看不出任何提示，所以必须先问一次。
    if (activeRequest?.status === 'selected' && activeRequest.voice_id) {
      const confirmed = await confirm({
        title: '该槽位已固化声线',
        message: <>
          「{activeRequest.name}」当前已经固化过声线，本次会用新选定的候选重新固化。
          <br />
          旧的声线不会消失，会留成历史版本（表格操作列的「查看历史声线」里可试听、可删除）；已经用它生成的成品也不会跟着变，仍标注「已换版」。
          <br />
          是否重新固化为新的声线？
        </>,
        confirmText: '重新固化',
        cancelText: '取消',
        danger: true,
      })
      if (!confirmed) return
    }
    // 固化涉及 GPU 提示词计算，耗时数秒；期间禁用按钮，避免连点重复固化。
    setFixing(true)
    try {
      const result = await apiPost<{ fixed: string[] }>(`/api/projects/${projectId}/voices/fix`, { request_id: activeRequestId })
      await voiceRequests.loadRequests()
      notify(`已固化声线「${result.fixed.join('、')}」`)
    } catch (error) {
      notify((error as Error).message)
    } finally {
      setFixing(false)
    }
  }

  /** 逐条解冻：清单里点哪条就解哪条，不必先把它加载进左侧表单。 */
  const reopenRequest = async (request: VoiceRequest) => {
    try {
      await apiPost(`/api/voice-requests/${request.id}/reopen`)
      await voiceRequests.loadRequests()
      if (activeRequestId === request.id) await candidates.loadCandidates(request.id)
      notify(`已解冻「${request.name}」，可重新生成与选择`)
    } catch (error) { notify((error as Error).message) }
  }

  const deleteRequest = async (request: VoiceRequest) => {
    if (!(await confirm({
      title: '删除声线槽位',
      message: `确认删除槽位「${request.name}」？已固化资产会保留。`,
      confirmText: '删除',
      danger: true,
    }))) return
    try {
      await apiDelete(`/api/projects/${projectId}/voice-requests/${request.id}`)
      // 删的是当前槽位就退回「＋ 新声线」态：表单与候选栏一并清空，
      // 否则残留的槽位名会让人以为还在编辑，候选按钮也会因没有 activeRequestId 而静默失效。
      if (activeRequestId === request.id) { setActiveRequestId(null); candidates.clear(); draft.resetForNewVoice() }
      await voiceRequests.loadRequests()
      notify('槽位已删除')
    } catch (error) { notify((error as Error).message) }
  }

  return <>
    <SectionTitle number="1" title="创建、挑选并固化声线" description="填写人物设定生成候选试听，选定后固化；新一轮生成会替换候选栏，跨轮比较请先暂存，或直接保留为单句成品。" />
    <div className="step-stack">
      <div className="panel-grid">
        <VoiceRequestForm
          requests={voiceRequests.requests}
          activeRequestId={activeRequestId}
          activeRequest={activeRequest}
          draft={draft}
          genRunning={generation.genRunning}
          genTask={generation.genTask}
          progress={voiceRequests.progress}
          notify={notify}
          onSelectSlot={handleSelectSlot}
          onBulkAdd={() => setBulkOpen(true)}
          onAttachLibrary={() => setLibOpen(true)}
          onGenerate={handleGenerate}
          onCancelGenerate={generation.cancelGenerate}
          onSaveDraft={saveDraft}
          onDeleteSlot={() => { if (activeRequest) deleteRequest(activeRequest) }}
          onTagsChanged={() => { voiceRequests.loadRequests().catch((error: Error) => notify(error.message)) }}
        />
        <CandidatePanel
          candidates={candidates.candidates}
          shortlisted={candidates.shortlisted}
          canFix={canFix}
          fixing={fixing}
          onSelect={select}
          onToggleShortlist={toggleShortlist}
          onKeepSingle={keepSingle}
          onFix={fix}
        />
      </div>
      {/* 收尾区已并入这张表：原来的「已固化声线」独立盒子删除，解冻改为行内操作。
          表标题栏只留「下一步」——两个建槽入口上移到左侧表单的「声线槽位」行，
          固化入口在右侧候选面板的暂存区标题栏，这里不再堆按钮。 */}
      <div className="panel panel-flush">
        <div className="panel-heading with-actions">
          声线槽位 <span className="panel-count">{voiceRequests.requests.length} 条</span>
          {onNext && <span className="panel-heading-actions">
            <Button primary onClick={onNext}>下一步：配音生成 →</Button>
          </span>}
        </div>
        <RequestTable
          requests={voiceRequests.requests}
          onLoad={loadRequest}
          onDelete={deleteRequest}
          onRename={renameRequest}
          onReopen={reopenRequest}
          onShowHistory={setHistoryTarget}
        />
      </div>
    </div>
    {bulkOpen && <BulkAddVoices
      existingNames={voiceRequests.requests.map((request) => request.name)}
      language={draft.language}
      onCancel={() => setBulkOpen(false)}
      onSubmit={addVoices}
    />}
    {libOpen && <AttachLibraryVoices
      existingVoiceIds={voiceRequests.requests.map((request) => request.voice_id).filter((id): id is string => Boolean(id))}
      onCancel={() => setLibOpen(false)}
      onSubmit={attachFromLibrary}
    />}
    {historyTarget && <VoiceHistoryDialog
      projectId={projectId}
      request={historyTarget}
      onClose={() => setHistoryTarget(null)}
      notify={notify}
      onChanged={() => { voiceRequests.loadRequests().catch(() => {}) }}
    />}
    {dialogElement}
  </>
}
