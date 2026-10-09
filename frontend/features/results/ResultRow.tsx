'use client'

import { fileUrl } from '@/lib/api'
import type { BatchItem, LineEdit } from '@/lib/types'
import { AudioPlayer, Button, LanguageSelect, StatusText } from '@/components/ui'

type ResultRowProps = {
  item: BatchItem
  edit: LineEdit
  busy: boolean
  onEditChange: (edit: LineEdit) => void
  onRegenerate: () => void
  onSelect: (selected: boolean) => void
}

/** 单条配音结果：行内改文本/语言后可重新生成，满意则选入成品。 */
export function ResultRow({ item, edit, busy, onEditChange, onRegenerate, onSelect }: ResultRowProps) {
  // 历史版本含当前版（最新在前）；这里只列比当前版旧的，供返修对比试听。
  const olderVersions = (item.versions ?? []).filter((entry) => entry.version !== item.version)
  return <article className={item.status === 'selected' ? 'product-row row-selected' : 'product-row'}>
    <div className="product-index">{String(item.position).padStart(4, '0')}</div>
    <div className="result-main">
      <div className="result-title">
        {item.voice_name}
        {item.version > 0 && <span className="version-badge" title={`当前第 ${item.version} 版，每返修一次版本号加一`}>v{item.version}</span>}
        <StatusText value={item.status} />
      </div>
      <div className="result-edit-row">
        <textarea className="line-edit" rows={2} value={edit.text} onChange={(event) => onEditChange({ ...edit, text: event.target.value })} />
        <LanguageSelect value={edit.language} onChange={(language) => onEditChange({ ...edit, language })} />
      </div>
      {olderVersions.length > 0 && <details className="version-history">
        <summary>历史版本（保留最近 {olderVersions.length + 1} 版，共 {olderVersions.length} 个旧版可对比）</summary>
        {olderVersions.map((entry) => <div className="version-row" key={entry.version}>
          <span className="version-badge">v{entry.version}</span>
          <AudioPlayer url={fileUrl(entry.url)} />
        </div>)}
      </details>}
    </div>
    <AudioPlayer url={fileUrl(item.output_url)} />
    <div className="cell-actions">
      <Button small disabled={busy} onClick={onRegenerate}>{busy ? '生成中…' : '重新生成'}</Button>
      {item.status === 'completed'
        ? <Button small primary onClick={() => onSelect(true)}>选入成品</Button>
        : <Button small onClick={() => onSelect(false)}>取消选定</Button>}
    </div>
  </article>
}
