'use client'

import { useEffect, useState } from 'react'
import { apiGet } from '@/lib/api'
import type { ModelHealth, SystemStatus } from '@/lib/types'
import { Button, SectionTitle } from '@/components/ui'

/** 「系统状态」页：本地服务、模型、GPU 与存储状态。 */
export default function SystemPage() {
  const [apiState, setApiState] = useState<'checking' | 'online' | 'offline'>('checking')
  const [status, setStatus] = useState<SystemStatus | null>(null)

  const refresh = async () => {
    setApiState('checking')
    try {
      const [health, system] = await Promise.all([
        apiGet<{ status: string; model: ModelHealth }>('/api/health'),
        apiGet<SystemStatus>('/api/system/status'),
      ])
      setStatus(system)
      setApiState('online')
    } catch {
      setApiState('offline')
    }
  }

  useEffect(() => { refresh() }, [])

  return <>
    <SectionTitle title="系统状态" description="查看本地服务、模型、GPU 与存储状态。模型在首次生成时才会加载。" />
    <Button onClick={() => refresh()}>刷新状态</Button>
    <div className="status-grid">
      <div><span>API 服务</span><strong className={apiState === 'online' ? 'success-text' : ''}>{apiState === 'online' ? '正常' : apiState === 'offline' ? '未连接' : '检查中'}</strong></div>
      <div><span>当前模型</span><strong>{status?.model.model ?? '未加载'}</strong></div>
      <div><span>CUDA</span><strong>{status ? (status.model.cuda ? '可用' : '不可用') : '—'}</strong></div>
      <div><span>GPU</span><strong>{status?.model.gpu ?? '—'}</strong></div>
      <div><span>显存占用 (GiB)</span><strong>{status?.model.allocated_gib ?? '—'}</strong></div>
      <div><span>项目数量</span><strong>{status?.projects ?? '—'}</strong></div>
    </div>
  </>
}
