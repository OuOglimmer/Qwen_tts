'use client'

import { fileUrl } from '@/lib/api'
import type { SingleOutput } from '@/lib/types'
import { AudioPlayer, Button } from '@/components/ui'

type SingleOutputsPanelProps = {
  singles: SingleOutput[]
  onDelete: (row: SingleOutput) => void
}

/** 单句成品列表：来自候选试听的「保留为单句成品」。 */
export function SingleOutputsPanel({ singles, onDelete }: SingleOutputsPanelProps) {
  return <div className="panel sub-panel">
    <div className="panel-heading">单句成品 <span>{singles.length} 条</span></div>
    {singles.length === 0 && <p className="hint">还没有单句成品。可以在「创建声线」步骤把候选试听保存为单句成品。</p>}
    {singles.map((row) => <article className="product-row" key={row.id}>
      <div className="product-index">{String(row.position).padStart(2, '0')}</div>
      <div className="product-info">
        <strong>{row.name}</strong>
        <p>{row.text}</p>
        <small>单句成品</small>
      </div>
      <AudioPlayer url={fileUrl(row.url)} />
      <div className="cell-actions">
        <Button small onClick={() => onDelete(row)}>删除</Button>
      </div>
    </article>)}
  </div>
}
