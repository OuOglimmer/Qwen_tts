export function StatusText({ value, kind = 'batch' }: { value: string; kind?: 'batch' | 'request' }) {
  if (kind === 'request') {
    if (value === 'fixed') return <span className="success-text">已固化</span>
    if (value === 'selected') return <span>已选定</span>
    if (value === 'candidate') return <span className="hint">候选中</span>
    if (value === 'ready') return <span className="hint">待生成</span>
    if (value === 'draft') return <span className="hint">草稿</span>
    if (value === 'single') return <span>单句成品</span>
    return <span>{value}</span>
  }
  if (value === 'completed') return <span className="hint">待选定</span>
  if (value === 'selected') return <span className="success-text">已选入成品</span>
  if (value === 'failed') return <span className="error-text">失败</span>
  if (value === 'running') return <span className="status-running">生成中</span>
  if (value === 'pending') return <span className="hint">等待</span>
  return <span>{value}</span>
}
