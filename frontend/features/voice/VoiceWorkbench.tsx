'use client'

import { useEffect, useState } from 'react'
import { apiDelete, apiGet, apiPost, apiPut } from '@/lib/api'
import type { Candidate, Notify, VoiceGenTask, VoiceRequest } from '@/lib/types'
import { Button, SectionTitle, useConfirmDialog } from '@/components/ui'
import { readDraft, readSeenTask, writeSeenTask } from './draft'
import { BulkAddVoices } from './BulkAddVoices'
import { CandidatePanel } from './CandidatePanel'
import { RequestTable } from './RequestTable'
import { VoiceRequestForm } from './VoiceRequestForm'
import { useVoiceCandidates } from './useVoiceCandidates'
import { useVoiceDraft } from './useVoiceDraft'
import { useVoiceGeneration } from './useVoiceGeneration'
import { useVoiceRequests } from './useVoiceRequests'

/**
 * 「声线」工作台（流程第 1 步）：组合槽位数据、草稿、候选与生成任务四个领域，
 * 自身只做编排——状态各归其 hook，展示各归其子组件。
 */
export default function VoiceWorkbench({ projectId, notify, onNext }: { projectId: string; notify: Notify; onNext?: () => void }) {
  const voiceRequests = useVoiceRequests(projectId)
  const [activeRequestId, setActiveRequestId] = useState<string | null>(null)
  const draft = useVoiceDraft(projectId, activeRequestId)
  const candidates = useVoiceCandidates()
  const generation = useVoiceGeneration({
    projectId,
    notify,
    activeRequestId,
    adoptRequest: setActiveRequestId,
    loadCandidates: candidates.loadCandidates,
    loadRequests: voiceRequests.loadRequests,
  })
  const [fixing, setFixing] = useState(false)
  const [bulkOpen, setBulkOpen] = useState(false)
  const { confirm, dialogElement } = useConfirmDialog()

  const activeRequest = voiceRequests.requests.find((request) => request.id === activeRequestId) ?? null

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
    else { setActiveRequestId(null); candidates.clear() }
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

  const select = async (candidateId: string) => {
    if (!activeRequestId) return
    try { await apiPost(`/api/voice-requests/${activeRequestId}/select/${candidateId}`); await candidates.loadCandidates(activeRequestId); await voiceRequests.loadRequests(); notify('已选定该候选，可继续固化') } catch (error) { notify((error as Error).message) }
  }

  const toggleShortlist = async (candidate: Candidate) => {
    if (!activeRequestId) return
    try {
      await apiPost(`/api/voice-requests/${activeRequestId}/candidates/${candidate.id}/shortlist`, { shortlisted: !candidate.shortlisted })
      await candidates.loadCandidates(activeRequestId); await voiceRequests.loadRequests()
    } catch (error) { notify((error as Error).message) }
  }

  const keepSingle = async (candidateId: string) => {
    if (!activeRequestId) return
    try {
      await apiPost<{ output_path: string; url: string | null }>(`/api/voice-requests/${activeRequestId}/candidates/${candidateId}/keep-single`)
      await voiceRequests.loadRequests(); notify('已保存为单句成品，可在「返修」或「成品」查看')
    } catch (error) { notify((error as Error).message) }
  }

  const fix = async () => {
    // 固化涉及 GPU 提示词计算，耗时数秒；期间禁用按钮，避免连点重复固化。
    setFixing(true)
    try {
      const result = await apiPost<{ fixed: string[] }>(`/api/projects/${projectId}/voices/fix`)
      await voiceRequests.loadRequests()
      notify(`已固化 ${result.fixed.length} 条声线`)
    } catch (error) {
      notify((error as Error).message)
    } finally {
      setFixing(false)
    }
  }

  const keepAll = async () => {
    try { const result = await apiPost<{ retained: string[] }>(`/api/projects/${projectId}/candidates/keep-all-single`); notify(`已保留 ${result.retained.length} 个单句成品`) } catch (error) { notify((error as Error).message) }
  }

  const reopen = async () => {
    if (!activeRequestId) { notify('请先加载一个已固化的声线槽位'); return }
    try { await apiPost(`/api/voice-requests/${activeRequestId}/reopen`); await voiceRequests.loadRequests(); await candidates.loadCandidates(activeRequestId); notify('已解冻，可重新生成与选择') } catch (error) { notify((error as Error).message) }
  }

  const deleteRequest = async (request: VoiceRequest) => {
    if (!(await confirm({
      title: '删除声线槽位',
      message: `确认删除槽位「${request.name}」？已固化资产会保留。`,
      confirmText: '删除',
      danger: true,
    }))) return
    try { await apiDelete(`/api/projects/${projectId}/voice-requests/${request.id}`); if (activeRequestId === request.id) setActiveRequestId(null); await voiceRequests.loadRequests(); notify('槽位已删除') } catch (error) { notify((error as Error).message) }
  }

  return <>
    <SectionTitle number="1" title="创建、挑选并固化声线" description="填写人物设定生成候选试听，选定后固化；新一轮生成会替换候选栏，跨轮比较请先暂存，或直接保留为单句成品。" />
    <div className="panel-grid">
      <VoiceRequestForm
        requests={voiceRequests.requests}
        activeRequestId={activeRequestId}
        activeRequest={activeRequest}
        draft={draft}
        genRunning={generation.genRunning}
        genTask={generation.genTask}
        progress={voiceRequests.progress}
        onSelectSlot={handleSelectSlot}
        onBulkAdd={() => setBulkOpen(true)}
        onGenerate={handleGenerate}
        onCancelGenerate={generation.cancelGenerate}
        onSaveDraft={saveDraft}
        onDeleteSlot={() => { if (activeRequest) deleteRequest(activeRequest) }}
      />
      <CandidatePanel
        candidates={candidates.candidates}
        shortlisted={candidates.shortlisted}
        onSelect={select}
        onToggleShortlist={toggleShortlist}
        onKeepSingle={keepSingle}
      />
    </div>
    <div className="section-heading">选定与固化</div>
    <p className="hint">全部人物选择完成后在这里统一固化；解冻会放弃当前槽位的固化选择、可重新生成，已有声线资产保留为历史版本。</p>
    <div className="button-row three">
      <Button primary disabled={fixing} onClick={fix}>{fixing ? '固化中…' : '固化当前项目所有已选声线'}</Button>
      <Button disabled={!activeRequest || activeRequest.status !== 'fixed'} onClick={reopen}>解冻当前槽位固化声线</Button>
      <Button onClick={keepAll}>全部已选试听保存为单句成品</Button>
    </div>
    <RequestTable requests={voiceRequests.requests} onLoad={loadRequest} onDelete={deleteRequest} onRename={renameRequest} />
    {onNext && <div className="button-row">
      <Button primary onClick={onNext}>下一步：配音生成 →</Button>
      <span className="hint">声线固化后即可在第 2 步绑定到台词批量生成。</span>
    </div>}
    {bulkOpen && <BulkAddVoices
      existingNames={voiceRequests.requests.map((request) => request.name)}
      language={draft.language}
      onCancel={() => setBulkOpen(false)}
      onSubmit={addVoices}
    />}
    {dialogElement}
  </>
}
