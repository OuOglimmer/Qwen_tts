'use client'

import { fileUrl } from '@/lib/api'
import type { Candidate } from '@/lib/types'
import { AudioPlayer, Button } from '@/components/ui'

type CandidatePanelProps = {
  candidates: Candidate[]
  shortlisted: Candidate[]
  onSelect: (candidateId: string) => void
  onToggleShortlist: (candidate: Candidate) => void
  onKeepSingle: (candidateId: string) => void
}

/** 右侧候选试听区：候选卡片网格 + 跨轮暂存对比面板。 */
export function CandidatePanel({ candidates, shortlisted, onSelect, onToggleShortlist, onKeepSingle }: CandidatePanelProps) {
  return <div className="wide-content">
    <div className="candidate-grid">
      {candidates.length === 0 && <p className="empty-state">还没有候选试听。填写左侧信息后点击「生成候选」。</p>}
      {candidates.map((candidate, index) => (
        <article className={candidate.selected ? 'candidate selected' : 'candidate'} key={candidate.id}>
          <div className="candidate-title"><span>候选 {index + 1}{candidate.selected ? ' · 已选定' : ''}</span><span>#{candidate.id.slice(0, 6)}</span></div>
          <AudioPlayer url={fileUrl(candidate.preview_url)} wide />
          <div className="candidate-actions">
            <Button small primary={!candidate.selected} selected={!!candidate.selected} onClick={() => onSelect(candidate.id)}>{candidate.selected ? '✓ 已选定' : '选定'}</Button>
            <Button small onClick={() => onToggleShortlist(candidate)}>{candidate.shortlisted ? '移出暂存' : '暂存'}</Button>
            <Button small onClick={() => onKeepSingle(candidate.id)}>单句成品</Button>
          </div>
        </article>
      ))}
    </div>
    {shortlisted.length > 0 && <div className="panel sub-panel">
      <div className="panel-heading">📌 暂存对比（跨轮比较）</div>
      <div className="candidate-grid compact">
        {shortlisted.map((candidate, index) => <div className={candidate.selected ? 'saved-card selected' : 'saved-card'} key={candidate.id}>
          <span>暂存 {index + 1}{candidate.selected ? ' · 已选为最终' : ''}</span>
          <AudioPlayer url={fileUrl(candidate.preview_url)} />
          <div className="cell-actions">
            <Button small primary={!candidate.selected} selected={!!candidate.selected} onClick={() => onSelect(candidate.id)}>{candidate.selected ? '✓ 已选为最终' : '选为最终'}</Button>
            <Button small onClick={() => onToggleShortlist(candidate)}>移出</Button>
          </div>
        </div>)}
      </div>
    </div>}
  </div>
}
