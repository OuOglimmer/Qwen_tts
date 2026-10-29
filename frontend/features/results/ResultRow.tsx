'use client'

import { useState } from 'react'
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
  /** 换用某个历史版本（后端会把它复制成新的一版，旧版仍留档）。 */
  onUseVersion: (version: number) => Promise<void>
}

/** 单条配音结果：行内改文本/语言后可重新生成，满意则选入成品；不满意可换回旧版。 */
export function ResultRow({ item, edit, busy, onEditChange, onRegenerate, onSelect, onUseVersion }: ResultRowProps) {
  // 逐行独立记录"正在换哪一版"，避免换用时整表按钮一起变文案。
  const [switching, setSwitching] = useState<number | null>(null)
  // 历史版本含当前版（最新在前）；这里只列比当前版旧的，供返修对比试听与换用。
  const olderVersions = (item.versions ?? []).filter((entry) => entry.version !== item.version)
  return <article className={item.status === 'selected' ? 'product-row result-row row-selected' : 'product-row result-row'}>
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
        <summary>历史版本（保留最近 {olderVersions.length + 1} 版，共 {olderVersions.length} 个旧版可试听或换用）</summary>
        {olderVersions.map((entry) => <div className="version-row" key={entry.version}>
          <span className="version-badge">v{entry.version}</span>
          <AudioPlayer url={fileUrl(entry.url)} />
          <Button
            small
            disabled={switching !== null}
            title="换回这一版：它会成为新的当前版，旧版全部保留"
            onClick={async () => {
              setSwitching(entry.version)
              try { await onUseVersion(entry.version) } finally { setSwitching(null) }
            }}
          >{switching === entry.version ? '换用中…' : '换用这一版'}</Button>
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
