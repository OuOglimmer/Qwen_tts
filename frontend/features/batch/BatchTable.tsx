'use client'

import { fileUrl } from '@/lib/api'
import type { BatchItem, LineEdit } from '@/lib/types'
import { AudioPlayer, Button, DataTable, LanguageSelect, StatusText } from '@/components/ui'

type BatchTableProps = {
  items: BatchItem[]
  drafts: Record<number, LineEdit>
  onDraftChange: (position: number, draft: LineEdit) => void
  onSelect: (item: BatchItem, selected: boolean) => void
  onDelete: (position: number) => void
}

const editable = (item: BatchItem) => item.status === 'pending' || item.status === 'failed'

/** 任务表：等待/失败的行支持行内改文本与语言，其余行只读展示。 */
export function BatchTable({ items, drafts, onDraftChange, onSelect, onDelete }: BatchTableProps) {
  return <DataTable
    headers={['序号', '声线', '台词', '语言', '状态', '错误', '试听', '操作']}
    rowKeys={items.map((item) => item.id)}
    rowClasses={items.map((item) => (item.status === 'selected' ? 'row-selected' : undefined))}
    rows={items.map((item) => {
      const draft = drafts[item.position] ?? { text: item.text, language: item.language }
      return [
        String(item.position).padStart(4, '0'),
        item.voice_name,
        editable(item)
          ? <textarea className="line-edit" rows={2} value={draft.text} onChange={(event) => onDraftChange(item.position, { ...draft, text: event.target.value })} />
          : <span className="line-text" title={item.text}>{item.text}</span>,
        editable(item)
          ? <LanguageSelect value={draft.language} onChange={(language) => onDraftChange(item.position, { ...draft, language })} />
          : item.language,
        <span className="status-with-version">
          <StatusText value={item.status} />
          {item.version > 0 && <span className="version-badge" title={`当前第 ${item.version} 版，返修一次版本号加一`}>v{item.version}</span>}
        </span>,
        <span className="error-text" title={item.error ?? ''}>{item.error ? (item.error.length > 18 ? `${item.error.slice(0, 18)}…` : item.error) : ''}</span>,
        <AudioPlayer url={fileUrl(item.output_url)} />,
        <span className="cell-actions">
          {item.status === 'completed' && <Button small primary onClick={() => onSelect(item, true)}>选入成品</Button>}
          {item.status === 'selected' && <Button small onClick={() => onSelect(item, false)}>取消选定</Button>}
          <Button small disabled={item.status === 'running'} onClick={() => onDelete(item.position)}>删除</Button>
        </span>,
      ]
    })}
  />
}
