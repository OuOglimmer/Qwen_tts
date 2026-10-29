'use client'

import { useEffect, useState } from 'react'
import type { Notify, TagGroup, VoiceGenTask, VoiceRequest } from '@/lib/types'
import { apiPost, apiPut } from '@/lib/api'
import { invalidateTagCache, loadTagGroups } from '@/lib/tags'
import { applyPromptAssist } from '@/lib/promptAssist'
import { Button, Label, LanguageSelect, Modal, PromptAssist, TagPicker, useConfirmDialog } from '@/components/ui'
import { PROMPT_PLACEHOLDER } from '@/lib/promptPresets'
import { MAX_CANDIDATES } from './useVoiceDraft'
import type { VoiceDraftState } from './useVoiceDraft'

type VoiceRequestFormProps = {
  requests: VoiceRequest[]
  activeRequestId: string | null
  activeRequest: VoiceRequest | null
  draft: VoiceDraftState
  genRunning: boolean
  genTask: VoiceGenTask
  progress: Record<string, number> | null
  notify: Notify
  onSelectSlot: (requestId: string | null) => void
  onBulkAdd: () => void
  /** 打开「从声线库添加」弹窗：把库里已有的声线挂成本项目的已固化槽位。 */
  onAttachLibrary: () => void
  onGenerate: () => void
  onCancelGenerate: () => void
  onSaveDraft: () => void
  onDeleteSlot: () => void
  /** 标签存盘后刷新槽位列表：表格与下一次切换都要拿到新的 tag_ids。 */
  onTagsChanged: () => void
}

/** 左侧控制面板：槽位选择、批量建槽、人物设定表单、生成/草稿操作与进度。 */
export function VoiceRequestForm({ requests, activeRequestId, activeRequest, draft, genRunning, genTask, progress, notify, onSelectSlot, onBulkAdd, onAttachLibrary, onGenerate, onCancelGenerate, onSaveDraft, onDeleteSlot, onTagsChanged }: VoiceRequestFormProps) {
  const { confirm, dialogElement } = useConfirmDialog()
  // 槽位标签：角色定位 / 出场分组（project 域）+ 需求优先级（request 域）。
  // 资产固有标签（性别年龄、音色特征）不在这里放——那是声线自己的属性，固化后才存在。
  const [tagGroups, setTagGroups] = useState<TagGroup[]>([])
  const [tagOpen, setTagOpen] = useState(false)
  const [tagDraft, setTagDraft] = useState<string[]>([])
  const [savingTags, setSavingTags] = useState(false)
  const slotTags = activeRequest?.tag_list ?? []
  // 换槽位（或标签在别处被改）时把草稿对齐过去。用字符串做依赖而不是数组本身：
  // tag_ids 每次渲染都是新数组，直接进 deps 会每次渲染都重置草稿，编辑当场被抹掉。
  const tagKey = `${activeRequest?.id ?? ''}|${(activeRequest?.tag_ids ?? []).join(',')}`

  useEffect(() => {
    // 只取 project / request 两个域：voice 域的组在这里选了也无处可去（要等固化才上移）。
    loadTagGroups()
      .then((groups) => setTagGroups(groups.filter((group) => group.scope !== 'voice')))
      .catch(() => setTagGroups([]))
  }, [])

  useEffect(() => { setTagDraft(activeRequest?.tag_ids ?? []) /* 换槽位时把草稿对齐到该槽位 */ }, [tagKey])

  const openTags = () => {
    setTagDraft(activeRequest?.tag_ids ?? [])
    setTagOpen(true)
  }

  const saveTags = async () => {
    if (!activeRequestId) return
    setSavingTags(true)
    try {
      await apiPut(`/api/voice-requests/${activeRequestId}/tags`, { tag_ids: tagDraft })
      invalidateTagCache()
      setTagOpen(false)
      onTagsChanged()
      notify('槽位标签已保存')
    } catch (error) { notify((error as Error).message) } finally { setSavingTags(false) }
  }

  const createTag = async (groupId: string, name: string): Promise<string | null> => {
    try {
      const created = await apiPost<{ id: string }>('/api/tags', { group_id: groupId, name })
      invalidateTagCache()
      const groups = await loadTagGroups(undefined, true)
      setTagGroups(groups.filter((group) => group.scope !== 'voice'))
      return created.id
    } catch (error) { notify((error as Error).message); return null }
  }

  // 模板/组装结果覆盖已有提示词前需应用内确认；配套试听文本只在为空时填入，不覆盖已写内容。
  const handlePromptAssist = (nextPrompt: string, nextAudition: string) =>
    applyPromptAssist({
      confirm,
      currentPrompt: draft.prompt,
      nextPrompt,
      nextAudition,
      currentAudition: draft.auditionText,
      setPrompt: draft.setPrompt,
      setAudition: draft.setAuditionText,
    })
  return <div className="control-panel">
    <div className="slot-label-row">
      <Label>声线槽位</Label>
      {/* 两个建槽入口并排：新建空槽位 / 从全局声线库挂已有声线（挂进来即为已固化）。 */}
      <span className="slot-label-actions">
        <Button small onClick={onBulkAdd}>＋ 添加多条声线</Button>
        <Button small onClick={onAttachLibrary}>＋ 添加已有声线</Button>
      </span>
    </div>
    <select value={activeRequestId ?? ''} onChange={(event) => onSelectSlot(event.target.value || null)}>
      <option value="">＋ 新声线（生成时自动创建）</option>
      {requests.map((request) => <option key={request.id} value={request.id}>{String(request.position).padStart(2, '0')} · {request.name} · {request.status}</option>)}
    </select>
    <Label>声线名称</Label>
    <input value={draft.name} onChange={(event) => draft.setName(event.target.value)} placeholder="例如：温柔旁白女生" />
    {/* 槽位标签放在名称下面、提示词上面：它是"这个角色是谁"的说明，读起来属于人物设定的一部分。
        胶囊只读展示 + 弹窗编辑——面板本身已经很长，再塞一整个标签选择器会把生成按钮推下去。 */}
    <div className="slot-tags">
      <span className="slot-tags-label">槽位标签</span>
      <span className="tag-list">
        {slotTags.length === 0
          // 说明放 title 不放正文：这一行必须保持单行，长文案会把「标签」按钮挤到第二行去。
          ? <span className="hint" title="给槽位打上角色定位 / 优先级；角色定位在固化时会上移到项目清单">未设置</span>
          : slotTags.map((tag) => <span key={tag.id} className="tag-chip" title={`${tag.group_name ?? ''} · ${tag.name}`}>
            {tag.color && <i className="tag-dot" style={{ background: tag.color }} />}
            <span className="tag-chip-text">{tag.name}</span>
          </span>)}
      </span>
      <Button small disabled={!activeRequestId} title={activeRequestId ? '编辑这个槽位的标签（角色定位 / 优先级）' : '先选中一个槽位'} onClick={openTags}>标签</Button>
    </div>
    <Label>声线提示词</Label>
    <PromptAssist onApply={handlePromptAssist} />
    <textarea value={draft.prompt} onChange={(event) => draft.setPrompt(event.target.value)} placeholder={PROMPT_PLACEHOLDER} />
    <Label>试听文本</Label>
    <textarea value={draft.auditionText} onChange={(event) => draft.setAuditionText(event.target.value)} placeholder="用于试听的一句话" />
    <div className="control-row">
      <div><Label>语言</Label>
        <LanguageSelect value={draft.language} onChange={draft.setLanguage} />
      </div>
      <div><Label>本轮候选数</Label>
        <input type="number" min={1} max={MAX_CANDIDATES} value={draft.count} onChange={(event) => draft.setCount(Number(event.target.value))} />
      </div>
    </div>
    <div className="button-row">
      <Button primary disabled={genRunning} onClick={onGenerate}>{genRunning ? '候选生成中…' : '生成候选'}</Button>
      {genRunning
        ? <Button onClick={onCancelGenerate}>取消生成</Button>
        : <Button disabled={!activeRequestId} onClick={onSaveDraft}>保存草稿</Button>}
    </div>
    {genTask && !genTask.finished && (() => {
      const processed = genTask.generated + genTask.failed
      const pct = genTask.total ? Math.round((processed / genTask.total) * 100) : 0
      return <div className="gen-progress">
        <div className="progress-track"><div className="progress-fill" style={{ width: `${pct}%` }} /></div>
        <span className="hint">正在生成「{genTask.name}」的候选：{processed}/{genTask.total}（{pct}%）</span>
      </div>
    })()}
    {activeRequest && <>
      <Label>删除当前槽位</Label>
      <div className="button-row">
        <Button onClick={onDeleteSlot}>删除槽位</Button>
      </div>
    </>}
    {progress && <p className="hint">进度：共 {progress.total} 个槽位 · 候选 {progress.candidate} · 已选 {progress.selected} · 已固化 {progress.fixed} · 单句成品 {progress.single}</p>}
    {tagOpen && <Modal
      title={`槽位标签 · ${activeRequest?.name ?? ''}`}
      fixed
      onClose={() => setTagOpen(false)}
      actions={<>
        <Button onClick={() => setTagOpen(false)}>取消</Button>
        <Button primary disabled={savingTags} onClick={() => void saveTags()}>{savingTags ? '保存中…' : '保存'}</Button>
      </>}
    >
      <p className="hint">「角色定位 / 出场分组」属于项目语义，「需求优先级」只跟着这个槽位；固化时前者会上移到项目清单，后者留在需求单上。</p>
      <TagPicker
        groups={tagGroups}
        value={tagDraft}
        onChange={setTagDraft}
        onCreate={createTag}
        hint="单选组再点一次可取消；新词条默认进「未分类」，可到标签组里再整理。"
      />
    </Modal>}
    {dialogElement}
  </div>
}
