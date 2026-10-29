'use client'

import { useEffect, useMemo, useState } from 'react'
import { apiGet, apiPost, fileUrl } from '@/lib/api'
import type { Notify, VoiceAsset } from '@/lib/types'
import { AudioPlayer, Button, Label, Modal } from '@/components/ui'

type AddLibraryVoiceProps = {
  projectId: string
  /** 本项目清单已有的声线 id，用于从候选中排除。 */
  rosterIds: string[]
  notify: Notify
  /** 关联成功后刷新清单。 */
  onAdded: () => void
}

/** 「从声线库添加声线」：把全局库里的现成声线关联进本项目清单（只建关联，不复制音频文件）。 */
export function AddLibraryVoice({ projectId, rosterIds, notify, onAdded }: AddLibraryVoiceProps) {
  const [open, setOpen] = useState(false)
  const [library, setLibrary] = useState<VoiceAsset[]>([])
  const [loadError, setLoadError] = useState('')
  const [loading, setLoading] = useState(false)
  const [query, setQuery] = useState('')
  const [sourceFilter, setSourceFilter] = useState('')
  const [voiceId, setVoiceId] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [busy, setBusy] = useState(false)

  const owned = useMemo(() => new Set(rosterIds), [rosterIds])
  const candidates = useMemo(() => library.filter((voice) => !owned.has(voice.id)), [library, owned])
  const sources = useMemo(() => Array.from(new Set(candidates.map((voice) => voice.source_display ?? '公共库'))), [candidates])
  const visible = useMemo(() => {
    const keyword = query.trim().toLowerCase()
    return candidates.filter((voice) => {
      if (sourceFilter && (voice.source_display ?? '公共库') !== sourceFilter) return false
      if (!keyword) return true
      return `${voice.name} ${voice.source_display ?? ''} ${voice.tags ?? ''}`.toLowerCase().includes(keyword)
    })
  }, [candidates, query, sourceFilter])

  const start = async () => {
    setOpen(true)
    setVoiceId('')
    setDisplayName('')
    setLoadError('')
    setLoading(true)
    // force：刚在别处固化过的声线必须立刻可选，命中 5 秒缓存会出现"新建的声线找不到"。
    try { setLibrary(await apiGet<VoiceAsset[]>('/api/library/voices', { force: true })) } catch (error) { setLoadError((error as Error).message) } finally { setLoading(false) }
  }

  const pick = (voice: VoiceAsset) => {
    setVoiceId(voice.id)
    setDisplayName(voice.name)
  }

  const confirm = async () => {
    if (!voiceId) { notify('请选择要添加的声线'); return }
    setBusy(true)
    try {
      const result = await apiPost<{ display_name: string }>(`/api/projects/${projectId}/voices/attach`, { voice_id: voiceId, display_name: displayName })
      setOpen(false)
      await onAdded()
      notify(`已将「${result.display_name}」加入本项目声线清单`)
    } catch (error) { notify((error as Error).message) } finally { setBusy(false) }
  }

  return <>
    <Button small onClick={() => start()}>＋ 从声线库添加声线</Button>
    {open && <Modal
      title="从声线库添加声线"
      wide
      onClose={() => setOpen(false)}
      actions={<>
        <Button onClick={() => setOpen(false)}>取消</Button>
        <Button primary disabled={busy || !voiceId} onClick={confirm}>添加到本项目</Button>
      </>}
    >
      {loadError && <p className="hint">{loadError}</p>}
      <div className="wizard-picker-bar">
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索声线名称/来源/标签…" />
        <select value={sourceFilter} onChange={(event) => setSourceFilter(event.target.value)}>
          <option value="">全部来源</option>
          {sources.map((source) => <option key={source} value={source}>{source}</option>)}
        </select>
      </div>
      <div className="wizard-voice-list">
        {/* 空态提示一律放在列表内部：
            ① 挂在列表外的话，出现/消失会撑动弹窗高度；
            ② 顺带补上"筛出来一条不剩"的提示——原来那种情况列表就是一片空白，没有任何说明。
            三态文案都要覆盖：拉取中 / 库里真的没有 / 只是筛选没命中。 */}
        {visible.length === 0 && (
          <p className="hint">
            {loading
              ? '正在加载声线库…'
              : candidates.length === 0
                ? '声线库中没有可添加的声线；可先到「上传声线」入库，或回步骤 1 新建声线。'
                : '没有符合条件的声线，换个关键词或来源试试。'}
          </p>
        )}
        {visible.map((voice) => {
          const picked = voiceId === voice.id
          return <div key={voice.id} className={picked ? 'wizard-voice picked' : 'wizard-voice'}>
            <label className="wizard-voice-check">
              <input type="radio" name="add-library-voice" checked={picked} onChange={() => pick(voice)} />
              <span title={voice.name}>{voice.name} <em className="hint">· {voice.source_display ?? '公共库'}</em></span>
            </label>
            <AudioPlayer url={fileUrl(voice.preview_url)} />
          </div>
        })}
      </div>
      {/* 常驻（未选中声线时压暗禁用）：原来它跟着 voiceId 条件渲染，
          选中任意一条声线弹窗就往下长高一截。 */}
      <div className={voiceId ? 'attach-display' : 'attach-display is-off'}>
        <Label>本项目清单名称（同项目内不可重名）</Label>
        <input value={displayName} disabled={!voiceId}
          onChange={(event) => setDisplayName(event.target.value)}
          placeholder={voiceId ? '' : '先在上方选中一条声线'} />
      </div>
    </Modal>}
  </>
}
