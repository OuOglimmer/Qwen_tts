'use client'

import { useEffect, useMemo, useState } from 'react'
import { apiGet } from '@/lib/api'
import type { VoiceAsset } from '@/lib/types'
import { Button, Modal } from '@/components/ui'

type AttachLibraryVoicesProps = {
  /** 已经在当前项目需求单里的 voice_id：这些行置灰，不允许重复挂。 */
  existingVoiceIds: string[]
  onCancel: () => void
  onSubmit: (voiceIds: string[]) => Promise<void>
}

/**
 * 「从声线库添加声线」弹窗（第 1 步专用）：列出全局库里的已固化声线，勾选后直接建成
 * 本项目的已固化槽位（= 这个角色的声线已经定了）。
 *
 * 这是「把已有声线拿进项目」的唯一高层入口：第 2 步的「从声线库添加」只挂项目清单，
 * 而「全部声线」页已不提供挂载按钮（那里只做查看 / 标签 / 删除）。
 * 这里会同时落需求单槽位，第 1 步的表格里能看到它、第 2 步能直接绑定。
 */
export function AttachLibraryVoices({ existingVoiceIds, onCancel, onSubmit }: AttachLibraryVoicesProps) {
  const [voices, setVoices] = useState<VoiceAsset[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [search, setSearch] = useState('')
  const [picked, setPicked] = useState<string[]>([])
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    let cancelled = false
    // 打开弹窗必须拿最新库存（刚在别处固化过），这里强制绕过 GET 缓存。
    apiGet<VoiceAsset[]>('/api/library/voices', { force: true })
      .then((rows) => { if (!cancelled) setVoices(rows) })
      .catch((err: Error) => { if (!cancelled) setError(err.message) })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [])

  const usedIds = useMemo(() => new Set(existingVoiceIds), [existingVoiceIds])

  const keyword = search.trim().toLowerCase()
  const filtered = useMemo(() => voices.filter((voice) => {
    if (!keyword) return true
    return [voice.name, voice.display_name, voice.source_display, voice.language, voice.tags]
      .join(' ').toLowerCase().includes(keyword)
  }), [voices, keyword])

  const toggle = (voice: VoiceAsset) => {
    if (usedIds.has(voice.id)) return
    setPicked((current) => current.includes(voice.id)
      ? current.filter((id) => id !== voice.id)
      : [...current, voice.id])
  }

  const submit = async () => {
    if (picked.length === 0 || submitting) return
    setSubmitting(true)
    try { await onSubmit(picked) } finally { setSubmitting(false) }
  }

  return <Modal
    title="从声线库添加声线 · 建为已固化槽位"
    wide
    fixed
    onClose={onCancel}
    actions={<>
      <Button onClick={onCancel}>取消</Button>
      <Button primary disabled={picked.length === 0 || submitting} onClick={() => void submit()}>
        {submitting ? '添加中…' : `添加 ${picked.length} 条`}
      </Button>
    </>}
  >
    <div className="lib-attach">
      <p className="hint">勾选的声线在这个项目里会直接成为「已固化」槽位——代表该角色的声线已经定了，可到第 2 步绑定台词配音，也可以随时在本页解冻后重新生成。</p>
      <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索声线名称、来源项目或标签…" />
      {loading && <p className="lib-empty">正在读取声线库…</p>}
      {!loading && error && <p className="lib-empty lib-error">{error}</p>}
      {!loading && !error && filtered.length === 0 && <p className="lib-empty">{voices.length === 0 ? '声线库还是空的：先去「上传声线」或第 1 步固化一条。' : '没有匹配的声线。'}</p>}
      {!loading && !error && filtered.length > 0 && <div className="lib-list">
        {filtered.map((voice) => {
          const used = usedIds.has(voice.id)
          const on = picked.includes(voice.id)
          return <button
            type="button"
            key={voice.id}
            className={`lib-row${on ? ' is-on' : ''}${used ? ' is-used' : ''}`}
            disabled={used}
            onClick={() => toggle(voice)}
          >
            <span className="lib-check" aria-hidden="true">{on ? '✓' : ''}</span>
            <span className="lib-name">{voice.display_name ?? voice.name}</span>
            <span className="lib-meta">{voice.source_display ?? '公共库'}</span>
            <span className="lib-meta">{voice.language}</span>
            {used && <span className="lib-badge">已在本项目</span>}
          </button>
        })}
      </div>}
    </div>
  </Modal>
}
