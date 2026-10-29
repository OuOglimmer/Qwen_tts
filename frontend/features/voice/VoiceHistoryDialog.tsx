'use client'

import { useEffect, useState } from 'react'
import { apiDelete, apiGet, fileUrl } from '@/lib/api'
import type { Notify, VoiceHistoryEntry, VoiceRequest } from '@/lib/types'
import { AudioPlayer, Button, Modal, formatTime, useConfirmDialog } from '@/components/ui'

type VoiceHistoryDialogProps = {
  projectId: string
  request: VoiceRequest
  onClose: () => void
  notify: Notify
  /** 删掉一条后让外面重拉槽位表：history_count 会跟着变，按钮可能要消失。 */
  onChanged: () => void
}

/**
 * 「查看历史声线」弹窗：这个槽位固化过、后来被换掉的旧声线。
 *
 * 它们的状态已是 replaced，全局库默认不列（library_voices 只给 status='active'），
 * 所以只能从槽位这一侧翻出来。删除直接复用全局库的删除接口 —— 那边会拦住
 * "还有成品引用"和"别的项目还在用"两种情况，这里只负责把不可点的按钮提前置灰。
 */
export function VoiceHistoryDialog({ projectId, request, onClose, notify, onChanged }: VoiceHistoryDialogProps) {
  const [rows, setRows] = useState<VoiceHistoryEntry[] | null>(null)
  const [error, setError] = useState('')
  const [busyId, setBusyId] = useState<string | null>(null)
  const { confirm, dialogElement } = useConfirmDialog()

  useEffect(() => {
    let cancelled = false
    // 打开弹窗要最新状态（刚换过版），强制绕过 GET 缓存。
    apiGet<VoiceHistoryEntry[]>(
      `/api/projects/${projectId}/voice-requests/${request.id}/history`,
      { force: true },
    )
      .then((data) => { if (!cancelled) setRows(data) })
      .catch((err: Error) => { if (!cancelled) { setRows([]); setError(err.message) } })
    return () => { cancelled = true }
  }, [projectId, request.id])

  const remove = async (row: VoiceHistoryEntry) => {
    const ok = await confirm({
      title: '删除这条历史声线',
      message: <>
        <p>确认删除「{row.name}」（{formatTime(row.created_at)} 固化的那一版）？</p>
        <p className="hint">参考音频与音色文件会一起从磁盘移除，删掉之后没法再拿它试听或复用了。</p>
      </>,
      confirmText: '删除',
      danger: true,
    })
    if (!ok) return
    setBusyId(row.id)
    try {
      await apiDelete(`/api/library/voices/${row.id}?project_id=${projectId}`)
      setRows((current) => (current ?? []).filter((item) => item.id !== row.id))
      notify(`已删除历史声线「${row.name}」`)
      onChanged()
    } catch (err) {
      notify((err as Error).message)
    } finally {
      setBusyId(null)
    }
  }

  return <>
    <Modal
      title={`历史声线 · ${request.name}`}
      fixed
      onClose={onClose}
      actions={<Button onClick={onClose}>关闭</Button>}
    >
      <p className="hint voice-history-hint">
        这个槽位之前固化过、后来被换掉的声线。它们已经不在全局声线库里列出，但音频都还在。
      </p>
      {rows === null && <p className="hint">加载中…</p>}
      {error && <p className="voice-history-error">{error}</p>}
      {rows !== null && rows.length === 0 && !error && <p className="hint">这个槽位还没有换过声线。</p>}
      {rows !== null && rows.length > 0 && <div className="voice-history-list">
        {rows.map((row) => <div className="voice-history-row" key={row.id}>
          <div className="voice-history-head">
            <span className="voice-history-name" title={row.name}>{row.name}</span>
            <span className="voice-history-time">{formatTime(row.created_at)}</span>
          </div>
          <div className="voice-history-foot">
            <AudioPlayer url={fileUrl(row.preview_url)} />
            <Button
              small
              danger
              disabled={busyId === row.id || row.bound_outputs > 0}
              title={row.bound_outputs > 0
                ? `还有 ${row.bound_outputs} 条成品用着这把声音，删掉它们的归属就断了`
                : '删掉这条历史声线及其音频文件'}
              onClick={() => void remove(row)}
            >{busyId === row.id ? '删除中…' : '删除'}</Button>
          </div>
        </div>)}
      </div>}
    </Modal>
    {dialogElement}
  </>
}
