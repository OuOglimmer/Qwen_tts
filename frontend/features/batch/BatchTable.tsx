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
  /** 空态文案：按角色筛选后该角色没有行时，要说清是"被筛掉了"。 */
  empty?: string
}

const editable = (item: BatchItem) => item.status === 'pending' || item.status === 'failed'

/** 任务表：等待/失败的行支持行内改文本与语言，其余行只读展示。 */
export function BatchTable({ items, drafts, onDraftChange, onSelect, onDelete, empty }: BatchTableProps) {
  return <DataTable
    headers={['序号', '声线', '台词', '语言', '状态', '错误', '试听', '操作']}
    empty={empty}
    rowKeys={items.map((item) => item.id)}
    rowClasses={items.map((item) => (item.status === 'selected' ? 'row-selected' : undefined))}
    rows={items.map((item) => {
      const draft = drafts[item.position] ?? { text: item.text, language: item.language }
      return [
        String(item.position).padStart(4, '0'),
        // 已换版：这条台词生成时用的声线已被新一版顶替，音频仍是旧声音。
        // 不拦着用户，只是让"第 27 条听起来和别的不一样"这种事在界面上有解释。
        <span className="voice-cell">
          {item.voice_name}
          {(item.roster_state === 'replaced' || item.voice_state === 'replaced') && <span
            className="stale-badge"
            title="生成这条时用的声线已经换版；音频仍是旧声音，需要新版声音请重新生成"
          >已换版</span>}
        </span>,
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
