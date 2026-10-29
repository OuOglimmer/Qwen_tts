'use client'

import { useCallback, useEffect, useMemo, useState } from 'react'
import { apiDelete, apiGet, apiPost, apiPut, fileUrl } from '@/lib/api'
import { invalidateTagCache, loadTagGroups, splitTags } from '@/lib/tags'
import { fetchActiveVoices } from '@/lib/voices'
import type { Notify, TagGroup, VoiceAsset } from '@/lib/types'
import { AudioPlayer, Button, DataTable, Label, Modal, SectionTitle, Select, TagPicker, formatTime, useConfirmDialog } from '@/components/ui'
import { DesignPanel } from './DesignPanel'
import { QuickGeneratePanel } from './QuickGeneratePanel'

/** 标签胶囊：有颜色画色点，没有就走默认蓝底（与项目内 chip 同一套视觉）。 */
function TagList({ tags, names }: { tags?: { name: string; color: string }[]; names?: string }) {
  const list = tags && tags.length > 0 ? tags : splitTags(names).map((name) => ({ name, color: '' }))
  if (list.length === 0) return <>—</>
  // 文字包一层 .tag-chip-text：超长标签才能在胶囊内省略，而不是把表格列撑宽
  return <span className="tag-list">{list.map((tag) => (
    <span key={tag.name} className="tag-chip" title={tag.name}>
      {tag.color && <i className="tag-dot" style={{ background: tag.color }} />}
      <span className="tag-chip-text">{tag.name}</span>
    </span>
  ))}</span>
}

/** 「全部声线」页：全局声线库。所有项目的固化资产集中在此，靠来源项目区分同名声线。 */
export function AssetsPage({ projectId, notify }: { projectId: string; notify: Notify }) {
  const [voices, setVoices] = useState<VoiceAsset[]>([])
  const [rosterVoices, setRosterVoices] = useState<VoiceAsset[]>([])
  const [search, setSearch] = useState('')
  const [source, setSource] = useState('')
  const [tagFilter, setTagFilter] = useState<string[]>([])
  const [tagMode, setTagMode] = useState<'any' | 'all'>('any')
  const [tagGroups, setTagGroups] = useState<TagGroup[]>([])
  const [editing, setEditing] = useState<VoiceAsset | null>(null)
  const [editingTags, setEditingTags] = useState<string[]>([])
  const [savingTags, setSavingTags] = useState(false)
  const { confirm, dialogElement } = useConfirmDialog()

  const refresh = useCallback(async () => {
    // 标签筛选走服务端：标签是"多对多"关系，前端过滤在库变大后会失真（也拿不到计数）。
    const query = tagFilter.length > 0
      ? `?tags=${encodeURIComponent(tagFilter.join(','))}&tag_mode=${tagMode}`
      : ''
    const [library, roster] = await Promise.all([
      apiGet<VoiceAsset[]>(`/api/library/voices${query}`),
      projectId ? fetchActiveVoices(projectId) : Promise.resolve([] as VoiceAsset[]),
    ])
    setVoices(library)
    setRosterVoices(roster)
  }, [projectId, tagFilter, tagMode])

  useEffect(() => { refresh().catch((error: Error) => notify(error.message)) }, [refresh, notify])
  useEffect(() => {
    loadTagGroups('voice').then(setTagGroups).catch(() => setTagGroups([]))
  }, [])

  const sources = useMemo(() => Array.from(new Set(voices.map((voice) => voice.source_display ?? '未知'))).sort(), [voices])
  const keyword = search.trim().toLowerCase()
  const filtered = voices.filter((voice) => {
    if (source && (voice.source_display ?? '未知') !== source) return false
    if (!keyword) return true
    return [voice.name, voice.source_display, voice.tags, voice.language, voice.ref_language ?? '', voice.notes, voice.source]
      .join(' ').toLowerCase().includes(keyword)
  })

  const toggleTag = (name: string) => {
    setTagFilter((previous) => previous.includes(name)
      ? previous.filter((item) => item !== name)
      : [...previous, name])
  }

  // 「加入已有声线」只在项目内做：第 1 步「＋ 添加已有声线」建槽位，
  // 第 2 步「从声线库添加」只挂清单。全局库这一页不提供挂载入口，
  // 否则同一件事有两个入口，且这里没有"移出项目"可以撤销。
  const remove = async (voice: VoiceAsset) => {
    if (!(await confirm({
      title: '删除声线资产',
      message: `确认从全局声线库删除「${voice.name}」（来源：${voice.source_display ?? '未知'}）？仍被任何项目清单或任务引用时会被拦截。已生成的音频文件不受影响。`,
      confirmText: '删除',
      danger: true,
    }))) return
    try { await apiDelete(`/api/library/voices/${voice.id}`); await refresh(); notify('声线资产已从全局库删除') } catch (error) { notify((error as Error).message) }
  }

  const openTagEditor = (voice: VoiceAsset) => {
    setEditing(voice)
    setEditingTags(voice.tag_ids ?? [])
  }

  const saveTags = async () => {
    if (!editing) return
    setSavingTags(true)
    try {
      await apiPut(`/api/library/voices/${editing.id}/tags`, { tag_ids: editingTags })
      invalidateTagCache()
      setTagGroups(await loadTagGroups('voice', true))
      setEditing(null)
      await refresh()
      notify(`已更新「${editing.name}」的标签`)
    } catch (error) { notify((error as Error).message) } finally { setSavingTags(false) }
  }

  const createTag = async (groupId: string, name: string): Promise<string | null> => {
    try {
      const created = await apiPost<{ id: string }>('/api/tags', { group_id: groupId, name })
      invalidateTagCache()
      setTagGroups(await loadTagGroups('voice', true))
      return created.id
    } catch (error) { notify((error as Error).message); return null }
  }

  return <>
    <SectionTitle title="全部声线" description="全局声线库：所有项目的固化声线都在此集中管理，同名声线靠来源项目区分。标签分「资产固有」与「项目语义」两层，这里是前者；项目里的角色定位请到该项目工作台设置。" />
    <div className="button-row asset-toolbar">
      <Button onClick={() => refresh().catch((error: Error) => notify(error.message))}>刷新声线库</Button>
      <div><Label>搜索</Label>
        <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="名称、来源、标签、语言或备注" />
      </div>
      <div><Label>来源项目</Label>
        <Select ariaLabel="按来源项目筛选" value={source} onChange={setSource}
          options={[{ value: '', label: '全部来源' }, ...sources.map((name) => ({ value: name, label: name }))]} />
      </div>
      <div><Label>标签匹配</Label>
        <Select ariaLabel="标签匹配方式" value={tagMode} onChange={(value) => setTagMode(value as 'any' | 'all')}
          options={[{ value: 'any', label: '任一命中' }, { value: 'all', label: '全部命中' }]} />
      </div>
    </div>
    {/* 标签筛选从单选下拉改成多选：标签本来就是多对多，"全部声线"里经常要同时看两三个特征。 */}
    <div className="tag-filter">
      <span className="tag-filter-label">按标签筛选</span>
      {tagGroups.flatMap((group) => group.tags).filter((tag) => (tag.usage ?? 0) > 0).length === 0
        ? <span className="hint">还没有声线打过标签，在表格的「标签」列可以补</span>
        : tagGroups.map((group) => {
          const used = group.tags.filter((tag) => (tag.usage ?? 0) > 0)
          if (used.length === 0) return null
          return <span className="tag-filter-group" key={group.id}>
            <em>{group.name}</em>
            {used.map((tag) => <button
              type="button"
              key={tag.id}
              className={`tag-option${tagFilter.includes(tag.name) ? ' is-on' : ''}`}
              title={`${group.name} · ${tag.name}（${tag.usage} 把声线）`}
              onClick={() => toggleTag(tag.name)}
            >
              {tag.color && <i className="tag-dot" style={{ background: tag.color }} />}
              {tag.name}
            </button>)}
          </span>
        })}
      {tagFilter.length > 0 && <Button small onClick={() => setTagFilter([])}>清空筛选</Button>}
    </div>
    <DataTable
      // 列名压短、去掉「项目ID」：10 列在原宽度下会被容器裁掉最后一个按钮，
      // 而 8 位 id 前辍对使用者没有意义（真要看来源点开项目即可）。
      headers={['试听', '名称', '来源项目', '试听语种', '素材语种', '标签', '引用', '创建时间', '操作']}
      rowKeys={filtered.map((voice) => voice.id)}
      rows={filtered.map((voice) => [
        <AudioPlayer url={fileUrl(voice.preview_url)} />,
        voice.display_name ?? voice.name,
        voice.source_display ?? '—',
        voice.language,
        voice.ref_language || '—',
        <TagList tags={voice.tag_list} names={voice.tags} />,
        voice.usage_count ?? 0,
        formatTime(voice.created_at),
        <div className="row-actions">
          <Button small onClick={() => openTagEditor(voice)}>标签</Button>
          <Button small danger onClick={() => remove(voice)}>删除</Button>
        </div>,
      ])}
    />
    {/* 本页三段：① 声线总库（上表）② 文字描述生成（造声线）③ 单句快速试听（用声线）。
        ②③ 各自带卡片标题，不再各加一个 SectionTitle —— 一层标题就够，叠两层反而分不清主次。
        ② 不依赖项目，没选项目时也能用；③ 要用项目清单里的声线，所以跟着 projectId 走。 */}
    <DesignPanel notify={notify} onArchived={() => { refresh().catch((error: Error) => notify(error.message)) }} />
    {projectId && <QuickGeneratePanel projectId={projectId} voices={rosterVoices} notify={notify} />}
    {editing && <Modal
      title={`编辑标签 · ${editing.name}`}
      fixed
      onClose={() => setEditing(null)}
      actions={<>
        <Button onClick={() => setEditing(null)}>取消</Button>
        <Button primary disabled={savingTags} onClick={() => void saveTags()}>{savingTags ? '保存中…' : '保存'}</Button>
      </>}
    >
      <p className="hint">这些是「资产固有」标签（性别年龄、音色特征），跨项目通用；「旁白 / 主角」这类角色语义属于项目，请到对应项目里设置。</p>
      <TagPicker
        groups={tagGroups}
        value={editingTags}
        onChange={setEditingTags}
        onCreate={createTag}
        hint="单选组再点一次可取消；新词条默认进「未分类」，可到标签组里再整理。"
      />
    </Modal>}
    {dialogElement}
  </>
}
