'use client'

import { useEffect, useState } from 'react'
import { loadTagGroups } from '@/lib/tags'
import type { TagGroup } from '@/lib/types'
import { Button } from './Button'
import { Label } from './Field'
import { Modal } from './Modal'
import { TagInput } from './TagInput'

export type VoiceDraft = { name: string; tags: string; notes: string }

/** 「登记声线」弹窗：入库前给声线命名并打标签。
 *  归属固定为公共库（project_id 传空）——声线库与项目解耦，入库时不必先想"归哪个项目"；
 *  要让某个项目用上它，到工作台第 1 步的「复用声线库」挂载即可。
 *  标签建议按「音色特征 / 性别年龄」分组给出：这两个是资产固有语义，跨项目成立。 */
export function VoiceRegisterDialog({ open, summary, suggestions, busy = false, onCancel, onSubmit }: {
  open: boolean
  /** 只读摘要：让弹窗遮住表单后，仍知道自己在给哪条参考音频命名。 */
  summary: string
  suggestions?: string[]
  busy?: boolean
  onCancel: () => void
  onSubmit: (draft: VoiceDraft) => void
}) {
  const [name, setName] = useState('')
  const [tags, setTags] = useState('')
  const [notes, setNotes] = useState('')
  const [groups, setGroups] = useState<TagGroup[]>([])

  // 每次重新打开都回到空表单：上一次的名称残留在下一次入库时只会造成误提交。
  useEffect(() => {
    if (!open) return
    setName(''); setTags(''); setNotes('')
    // 标签组是次要数据，拉不到就退化成一串自由标签，不阻塞入库。
    loadTagGroups('voice').then(setGroups).catch(() => setGroups([]))
  }, [open])

  if (!open) return null

  return <Modal
    title="登记声线"
    fixed
    onClose={onCancel}
    actions={<>
      <Button onClick={onCancel}>取消</Button>
      <Button primary disabled={!name.trim() || busy} onClick={() => onSubmit({ name: name.trim(), tags, notes })}>
        {busy ? '入库中…' : '确认入库'}
      </Button>
    </>}
  >
    <Label>声线名称（必填）</Label>
    <input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：林黛玉 · 试录版" />
    <Label>标签</Label>
    <TagInput value={tags} onChange={setTags} suggestions={suggestions} groups={groups} />
    <Label>备注</Label>
    <textarea value={notes} onChange={(event) => setNotes(event.target.value)} placeholder="可选" />
    <p className="hint">{summary}</p>
    <p className="hint">入库到公共声线库，不自动关联任何项目。</p>
  </Modal>
}
