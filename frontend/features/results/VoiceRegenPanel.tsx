'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import { apiGet, apiPost } from '@/lib/api'
import { usePolling } from '@/lib/hooks'
import type { Notify, RegenProgress, VoiceAsset } from '@/lib/types'
import { Button, VoiceSelect } from '@/components/ui'

type VoiceRegenPanelProps = {
  projectId: string
  voices: VoiceAsset[]
  notify: Notify
  /** 返修完成后刷新外部结果列表。 */
  onRegenerated: () => Promise<void>
  /** 页面可见时为 true；隐藏（keep-alive 切走）时暂停轮询。 */
  active?: boolean
  /** 上方角色筛选选中的那把声线：跟随它，省一次手动选。 */
  preferredVoiceId?: string
}

/** 按人物批量返修：用当前声线把该人物全部已有台词重新生成一遍。 */
export function VoiceRegenPanel({ projectId, voices, notify, onRegenerated, active, preferredVoiceId }: VoiceRegenPanelProps) {
  const [regenVoiceId, setRegenVoiceId] = useState('')
  const [regenProgress, setRegenProgress] = useState<RegenProgress>(null)
  const progressSignature = useRef('')
  // appliedPreferred 记住"已经跟随过哪个筛选": voices 每次刷新都是新数组引用，不加这道门
  // 就会把用户手动改过的下拉反复按回筛选值（与 BatchComposer 对齐语言同一个手法）。
  const appliedPreferred = useRef<string | null>(null)

  useEffect(() => {
    if (!preferredVoiceId || appliedPreferred.current === preferredVoiceId) return
    appliedPreferred.current = preferredVoiceId
    if (voices.some((voice) => voice.id === preferredVoiceId)) setRegenVoiceId(preferredVoiceId)
  }, [preferredVoiceId, voices])

  const pollRegeneration = useCallback(async (force = false) => {
    const progress = await apiGet<RegenProgress>('/api/voice-regeneration/progress', { force })
    // 签名门控：进度没变化不 setState，返修卡住时页面不再每 2 秒重渲染。
    const signature = progress ? JSON.stringify(progress) : ''
    if (signature !== progressSignature.current) {
      progressSignature.current = signature
      setRegenProgress(progress)
    }
    return progress
  }, [])

  // 挂载时同步一次进度，重新进入页面能接上仍在进行的返修。
  useEffect(() => { pollRegeneration().catch(() => undefined) }, [pollRegeneration])

  const regenRunning = Boolean(regenProgress && !regenProgress.done)
  // 轮询带 force 保证返修进度实时；active=false（页面被 keep-alive 切走）时暂停。
  usePolling(async () => {
    const progress = await pollRegeneration(true)
    if (progress?.done) {
      if (progress.error) notify(`批量返修异常：${progress.error}`)
      else if (progress.cancelled) notify(`批量返修已取消：成功 ${progress.succeeded}，失败 ${progress.failed}`)
      else notify(`批量返修完成：成功 ${progress.succeeded}，失败 ${progress.failed}`)
      await onRegenerated()
    }
  }, 2000, regenRunning && (active ?? true))

  const cancelRegen = async () => {
    try {
      await apiPost('/api/voice-regeneration/cancel')
      notify('已请求取消，当前句生成完成后停止')
    } catch (error) { notify((error as Error).message) }
  }

  const startVoiceRegeneration = async () => {
    if (!regenVoiceId) { notify('请选择要返修的声线'); return }
    try {
      await apiPost(`/api/projects/${projectId}/voices/${regenVoiceId}/regenerate`)
      notify('按人物批量返修已开始，后台执行中…')
      await pollRegeneration()
    } catch (error) { notify((error as Error).message) }
  }

  return <div className="panel sub-panel">
    <div className="panel-heading">按人物批量返修 <span>用当前声线把该人物全部已有台词重新生成一遍</span></div>
    <div className="button-row regen-row">
      <VoiceSelect
        voices={voices}
        value={regenVoiceId}
        onChange={setRegenVoiceId}
        placeholder={voices.length === 0 ? '本项目还没有生成过配音的声线' : '选择声线'}
        placeholderAlways
        className="compact-select"
      />
      <Button primary disabled={regenRunning} onClick={startVoiceRegeneration}>
        {regenRunning ? '返修进行中…' : '开始批量返修'}
      </Button>
      {regenRunning && <Button onClick={cancelRegen}>取消返修</Button>}
      {regenProgress && <span className="hint">
        {regenProgress.done
          ? `上次「${regenProgress.voice_name}」：成功 ${regenProgress.succeeded}，失败 ${regenProgress.failed}${regenProgress.cancelled ? '（已取消）' : ''}`
          : `正在返修「${regenProgress.voice_name}」：${regenProgress.processed}/${regenProgress.total} · 成功 ${regenProgress.succeeded} · 失败 ${regenProgress.failed}`}
      </span>}
    </div>
  </div>
}
