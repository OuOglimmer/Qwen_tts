'use client'

import { fileUrl } from '@/lib/api'
import type { Candidate } from '@/lib/types'
import { AudioPlayer, Button } from '@/components/ui'

type CandidatePanelProps = {
  candidates: Candidate[]
  shortlisted: Candidate[]
  /** 当前槽位是否已选定候选（status === 'selected'）。固化按钮只认当前槽位。 */
  canFix: boolean
  fixing: boolean
  /** 点一下选定、再点一下取消（同一张卡上的开关）。 */
  onSelect: (candidateId: string, selected: boolean) => void
  onToggleShortlist: (candidate: Candidate) => void
  onKeepSingle: (candidateId: string) => void
  onFix: () => void
}

/** 右侧候选试听区：候选横向轨道（一行 4 个，超出左右滚）+ 跨轮暂存纵向轨道（超出上下滚）。
 *  整块和左侧表单一样装在同一个大盒子里（.panel-flush = 列表型大盒子），
 *  标题栏承担留白，空态提示也留在盒内，不再裸放在页面上。 */
export function CandidatePanel({ candidates, shortlisted, canFix, fixing, onSelect, onToggleShortlist, onKeepSingle, onFix }: CandidatePanelProps) {
  return <div className="panel panel-flush candidate-panel">
    <div className="panel-heading">候选试听 <span>{candidates.length > 0 ? `${candidates.length} 个` : '待生成'}</span></div>
    {candidates.length === 0 && <p className="empty-state">还没有候选试听。填写左侧信息后点击「生成候选」。</p>}
    {candidates.length > 0 && (
      <div className="candidate-rail">
        {candidates.map((candidate, index) => (
          <article className={candidate.selected ? 'candidate selected' : 'candidate'} key={candidate.id}>
            <div className="candidate-title"><span>候选 {index + 1}{candidate.selected ? ' · 已选定' : ''}{candidate.shortlisted ? ' · 已暂存' : ''}</span><span>#{candidate.id.slice(0, 6)}</span></div>
            <AudioPlayer url={fileUrl(candidate.preview_url)} wide />
            <div className="candidate-actions">
              <Button
                small
                primary={!candidate.selected}
                selected={!!candidate.selected}
                title={candidate.selected ? '再点一次取消选定（当前槽位不再固化）' : '选定这条候选，随后可固化'}
                onClick={() => onSelect(candidate.id, !candidate.selected)}
              >{candidate.selected ? '✓ 已选定' : '选定'}</Button>
              <Button small onClick={() => onToggleShortlist(candidate)}>{candidate.shortlisted ? '移出暂存' : '暂存'}</Button>
              <Button small onClick={() => onKeepSingle(candidate.id)}>单句成品</Button>
            </div>
          </article>
        ))}
      </div>
    )}
    {/* 暂存区常驻：它是个"看不到就会忘记"的容器，空着也要留个位置说明用法，
        否则用户点了暂存、界面又没变化时会以为按钮坏了（条件渲染 + 缓存重叠时踩过）。 */}
    <div className="shortlist-block">
      {/* 固化入口放在这里而不是槽位表标题栏：选定动作就在上面的候选卡上，
          选完就地固化，不用跨到左侧表格去找按钮。
          按钮带「已选定 N」计数——暂存池里的候选与此无关，靠计数避免被误读成"固化暂存"。 */}
      <div className="panel-heading">
        📌 暂存对比（跨轮比较）{shortlisted.length > 0 && <span> {shortlisted.length} 个</span>}
        <span className="panel-heading-actions">
          <Button
            small
            primary
            disabled={fixing || !canFix}
            title={!canFix
              ? '当前槽位还没有「已选定」的候选：先在候选卡上点「选定」，这里才能固化'
              : '固化当前槽位选定的候选；暂存池里的候选不参与'}
            onClick={onFix}
          >{fixing ? '固化中…' : '固化当前声线'}</Button>
        </span>
      </div>
      {shortlisted.length === 0 && <p className="shortlist-empty">暂无暂存。点候选卡上的「暂存」，这一条会在下一轮生成之后仍然留在这里，方便 AB 对比。</p>}
      {shortlisted.length > 0 && <div className="shortlist-rail">
        {shortlisted.map((candidate, index) => <article className={candidate.selected ? 'candidate saved selected' : 'candidate saved'} key={candidate.id}>
          <div className="candidate-title"><span>暂存 {index + 1}{candidate.selected ? ' · 已选为最终' : ''}</span><span>#{candidate.id.slice(0, 6)}</span></div>
          <AudioPlayer url={fileUrl(candidate.preview_url)} wide />
          <div className="candidate-actions pair">
            <Button
              small
              primary={!candidate.selected}
              selected={!!candidate.selected}
              title={candidate.selected ? '再点一次取消选定' : '把这条暂存选为该槽位的最终候选'}
              onClick={() => onSelect(candidate.id, !candidate.selected)}
            >{candidate.selected ? '✓ 已选为最终' : '选为最终'}</Button>
            <Button small onClick={() => onToggleShortlist(candidate)}>移出</Button>
          </div>
        </article>)}
      </div>}
    </div>
  </div>
}
