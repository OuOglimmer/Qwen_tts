'use client'

import { useState } from 'react'
import { fileUrl } from '@/lib/api'
import type { VoiceRequest } from '@/lib/types'
import { Button, DataTable, StatusText, formatTime } from '@/components/ui'

type RequestTableProps = {
  requests: VoiceRequest[]
  onLoad: (request: VoiceRequest) => void
  onDelete: (request: VoiceRequest) => void
  onRename: (request: VoiceRequest, name: string) => void
}

/** 底部工作表：全部声线槽位的总览、改名与操作。 */
export function RequestTable({ requests, onLoad, onDelete, onRename }: RequestTableProps) {
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingName, setEditingName] = useState('')

  const startEdit = (request: VoiceRequest) => {
    setEditingId(request.id)
    setEditingName(request.name)
  }
  const finishEdit = (request: VoiceRequest, commit: boolean) => {
    setEditingId(null)
    const next = editingName.trim()
    if (commit && next && next !== request.name) onRename(request, next)
  }

  return <DataTable
    headers={['序号', '名称', '状态', '候选', '暂存', '单句成品', '更新时间', '操作']}
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
              if (event.key === 'Escape') setEditingId(null)
            }}
          />
        : request.name,
      <StatusText value={request.status} kind="request" />,
      String(request.candidate_count),
      String(request.shortlisted_count),
      request.direct_output_url ? <a className="link-a" href={fileUrl(request.direct_output_url) ?? '#'} target="_blank" rel="noreferrer">试听</a> : '—',
      formatTime(request.updated_at ?? undefined),
      <span className="cell-actions">
        <Button small onClick={() => onLoad(request)}>加载</Button>
        {/* 固化后的名称已复制进声线资产，改槽位名会让两者对不上，需先解冻。 */}
        <Button small disabled={request.status === 'fixed'} title={request.status === 'fixed' ? '已固化声线请先解冻后再改名' : undefined} onClick={() => startEdit(request)}>改名</Button>
        <Button small onClick={() => onDelete(request)}>删除</Button>
      </span>,
    ])}
    rowKeys={requests.map((request) => request.id)}
  />
}
