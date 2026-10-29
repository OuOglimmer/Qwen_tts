'use client'

import { useRef, useState } from 'react'
import { fileUrl } from '@/lib/api'
import type { Tag, VoiceRequest } from '@/lib/types'
import { Button, DataTable, StatusText, formatTime } from '@/components/ui'

type RequestTableProps = {
  requests: VoiceRequest[]
  onLoad: (request: VoiceRequest) => void
  onDelete: (request: VoiceRequest) => void
  onRename: (request: VoiceRequest, name: string) => void
  /** 解冻已固化的槽位：状态退回 candidate，旧声线资产留成 replaced 历史版本。 */
  onReopen: (request: VoiceRequest) => void
  /** 查看这个槽位换掉过的旧声线（replaced 资产，全局库不再列出）。 */
  onShowHistory: (request: VoiceRequest) => void
}

/** 槽位标签：表格里只放前两个，其余收成 +N（标签是自由词条，数量不可控）。 */
function SlotTags({ tags }: { tags?: Tag[] }) {
  if (!tags || tags.length === 0) return <>—</>
  const shown = tags.slice(0, 2)
  const rest = tags.slice(2)
  return <span className="tag-list">
    {shown.map((tag) => <span className="tag-chip" key={tag.id} title={tag.group_name ? `${tag.group_name} · ${tag.name}` : tag.name}>
      {tag.color && <i className="tag-dot" style={{ background: tag.color }} />}
      <span className="tag-chip-text">{tag.name}</span>
    </span>)}
    {rest.length > 0 && <span className="tag-chip" title={rest.map((tag) => tag.name).join('、')}>+{rest.length}</span>}
  </span>
}

/** 底部工作表：全部声线槽位的总览、改名与操作。 */
export function RequestTable({ requests, onLoad, onDelete, onRename, onReopen, onShowHistory }: RequestTableProps) {
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingName, setEditingName] = useState('')
  // 用 ref 记"这一行确实还在编辑中"：Enter 提交后输入框卸载会再触发一次 blur，
  // 光看 state 闭包拿到的还是旧值，会重复发一次 PUT；Esc 取消时也得同步清掉，
  // 否则随后的 blur 会把刚取消的改名又提交上去。
  const editingRef = useRef<string | null>(null)

  const startEdit = (request: VoiceRequest) => {
    editingRef.current = request.id
    setEditingId(request.id)
    setEditingName(request.name)
  }
  const cancelEdit = () => {
    editingRef.current = null
    setEditingId(null)
  }
  const finishEdit = (request: VoiceRequest, commit: boolean) => {
    if (editingRef.current !== request.id) return
    editingRef.current = null
    setEditingId(null)
    const next = editingName.trim()
    if (commit && next && next !== request.name) onRename(request, next)
  }

  return <DataTable
    headers={['序号', '名称', '标签', '状态', '候选', '暂存', '单句成品', '更新时间', '操作']}
    rows={requests.map((request) => [
      String(request.position).padStart(2, '0'),
      editingId === request.id
        ? <input
            className="name-edit"
            autoFocus
            value={editingName}
            onChange={(event) => setEditingName(event.target.value)}
            onBlur={() => finishEdit(request, true)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') finishEdit(request, true)
              if (event.key === 'Escape') cancelEdit()
            }}
          />
        : request.name,
      <SlotTags tags={request.tag_list} />,
      <StatusText value={request.status} kind="request" />,
      String(request.candidate_count),
      String(request.shortlisted_count),
      request.direct_output_url ? <a className="link-a" href={fileUrl(request.direct_output_url) ?? '#'} target="_blank" rel="noreferrer">试听</a> : '—',
      formatTime(request.updated_at ?? undefined),
      <span className="cell-actions">
        <Button small onClick={() => onLoad(request)}>加载</Button>
        {/* 固化后的名称已复制进声线资产，改槽位名会让两者对不上，需先解冻。 */}
        <Button small disabled={request.status === 'fixed'} title={request.status === 'fixed' ? '已固化声线请先解冻后再改名' : undefined} onClick={() => startEdit(request)}>改名</Button>
        {/* 解冻原先只在「已固化声线」独立盒子里，那个盒子删掉后搬到行内，否则已固化槽位没有回头路。 */}
        {request.status === 'fixed' && <Button small title="解冻后这个槽位可重新生成与选择，已有声线资产保留为历史版本" onClick={() => onReopen(request)}>解冻</Button>}
        {/* 换过声线才出现：没换过的槽位点开只会看到空列表。 */}
        {(request.history_count ?? 0) > 0 && <Button
          small
          title={`这个槽位换掉过 ${request.history_count} 把声线，点开可试听或删除`}
          onClick={() => onShowHistory(request)}
        >查看历史声线</Button>}
        <Button small onClick={() => onDelete(request)}>删除</Button>
      </span>,
    ])}
    rowKeys={requests.map((request) => request.id)}
  />
}
