'use client'

import { useCallback, useEffect, useState } from 'react'
import { apiDelete, apiGet, fileUrl } from '@/lib/api'
import type { Notify, VoiceAsset } from '@/lib/types'
import { AudioPlayer, Button, DataTable, Label, SectionTitle, formatTime, useConfirmDialog } from '@/components/ui'
import { QuickGeneratePanel } from './QuickGeneratePanel'

/** 「声线资产」页：当前项目的全部固化声线，可搜索、试听、删除与快速生成。 */
export function AssetsPage({ projectId, notify }: { projectId: string; notify: Notify }) {
  const [voices, setVoices] = useState<VoiceAsset[]>([])
  const [search, setSearch] = useState('')
  const { confirm, dialogElement } = useConfirmDialog()

  const refresh = useCallback(async () => {
    setVoices(await apiGet<VoiceAsset[]>(`/api/projects/${projectId}/voices`))
  }, [projectId])

  useEffect(() => { refresh().catch((error: Error) => notify(error.message)) }, [projectId])

  const keyword = search.trim().toLowerCase()
  const filtered = keyword
    ? voices.filter((voice) => [voice.name, voice.tags, voice.language, voice.notes, voice.source]
      .join(' ').toLowerCase().includes(keyword))
    : voices

  const remove = async (voice: VoiceAsset) => {
    if (!(await confirm({
      title: '删除声线资产',
      message: `确认删除声线资产「${voice.name}」？已生成的音频文件会保留。`,
      confirmText: '删除',
      danger: true,
    }))) return
    try { await apiDelete(`/api/projects/${projectId}/voices/${voice.id}`); await refresh(); notify('声线资产已删除') } catch (error) { notify((error as Error).message) }
  }

  return <>
    <SectionTitle title="声线资产库" description="当前项目的全部固化声线，可搜索、试听、删除，或直接用某个声线快速生成一段 MP3。" />
    <div className="button-row asset-toolbar">
      <Button onClick={() => refresh().catch((error: Error) => notify(error.message))}>刷新声线库</Button>
      <div><Label>搜索</Label>
        <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="名称、标签、语言或备注" />
      </div>
    </div>
    <DataTable
      headers={['试听', '名称', '来源', '语言', '标签', '状态', '创建时间', 'ID', '操作']}
      rowKeys={filtered.map((voice) => voice.id)}
      rows={filtered.map((voice) => [
        <AudioPlayer url={fileUrl(voice.preview_url)} />,
        voice.name,
        voice.source,
        voice.language,
        voice.tags || '—',
        voice.status === 'active' ? '已固化' : voice.status,
        formatTime(voice.created_at),
        voice.id.slice(0, 8),
        <Button small onClick={() => remove(voice)}>删除</Button>,
      ])}
    />
    <div className="section-heading">使用已归档声线快速生成</div>
    <div className="panel-grid asset-generate">
      <QuickGeneratePanel projectId={projectId} voices={voices} notify={notify} />
    </div>
    {dialogElement}
  </>
}
