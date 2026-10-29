'use client'

import { useEffect, useMemo, useState } from 'react'
import { apiGet, fileUrl } from '@/lib/api'
import { loadTagGroups, voiceTagNames } from '@/lib/tags'
import type { Notify, TagGroup, VoiceAsset } from '@/lib/types'
import { Button, AudioPlayer } from '@/components/ui'

type Mode = 'A' | 'B'

/** 筛选口径二选一：按来源项目 / 按标签。互斥，不叠加。 */
type Dimension = 'project' | 'tag'

type Selected = { voiceId: string; displayName: string }

type NewProjectWizardProps = {
  onClose: () => void
  /** 真正的创建动作由组合根的 useProjects.createProject 执行（它掌管新建落点标记）。
   *  返回创建出的项目 id 与声线挂载失败明细。 */
  onCreate: (name: string, picks: { voiceId: string; displayName: string }[]) => Promise<{ id: string; failed: string[] }>
  notify: Notify
}

/** 新建项目向导：第 1 步名称+模式；B 模式第 2 步全局声线选择器（两重区分：按项目/按标签 + 搜索 + 试听 + chips + 重名拦截）。
 *  项目在向导完成时才真正创建，所选声线只建清单关联，不复制文件。 */
export function NewProjectWizard({ onClose, onCreate, notify }: NewProjectWizardProps) {
  const [step, setStep] = useState<1 | 2>(1)
  const [name, setName] = useState('')
  const [mode, setMode] = useState<Mode>('A')
  const [library, setLibrary] = useState<VoiceAsset[]>([])
  const [loadError, setLoadError] = useState('')
  const [query, setQuery] = useState('')
  // 两重区分：维度互斥，各自记住自己的选择（切回来还在），但只应用当前维度那一个。
  const [dimension, setDimension] = useState<Dimension>('project')
  const [projectFilter, setProjectFilter] = useState('')
  const [tagFilter, setTagFilter] = useState<string[]>([])
  const [tagGroups, setTagGroups] = useState<TagGroup[]>([])
  // 标签列表是异步来的：区分"还没读到"和"库里真没有标签"，空态文案才不会说错话。
  const [tagsLoaded, setTagsLoaded] = useState(false)
  const [selected, setSelected] = useState<Selected[]>([])
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    apiGet<VoiceAsset[]>('/api/library/voices')
      .then(setLibrary)
      .catch((error: Error) => setLoadError(error.message))
  }, [])

  // 标签接口挂了不该让整个选声线步骤瘫掉：失败就当作"没有标签"，项目维度照常用。
  useEffect(() => {
    loadTagGroups('voice')
      .then(setTagGroups)
      .catch(() => undefined)
      .finally(() => setTagsLoaded(true))
  }, [])

  /** 按来源项目分面：公共库单独一项，已删除的来源项目保留原名（后端已带「（已删除）」）。 */
  const projectFacets = useMemo(() => {
    const counts = new Map<string, number>()
    for (const voice of library) {
      const label = voice.source_display ?? '公共库'
      counts.set(label, (counts.get(label) ?? 0) + 1)
    }
    return [...counts.entries()]
      .map(([label, count]) => ({ label, count }))
      .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label, 'zh'))
  }, [library])

  /** 标签分面：只列库内真用得上的词条（计数从当前库现算，不信 usage 的全局累计）。 */
  const tagFacets = useMemo(() => {
    const counts = new Map<string, number>()
    for (const voice of library) {
      for (const name of voiceTagNames(voice)) counts.set(name, (counts.get(name) ?? 0) + 1)
    }
    return tagGroups
      .map((group) => ({
        id: group.id,
        name: group.name,
        tags: group.tags
          .map((tag) => ({ id: tag.id, name: tag.name, color: tag.color, count: counts.get(tag.name) ?? 0 }))
          .filter((tag) => tag.count > 0),
      }))
      .filter((group) => group.tags.length > 0)
  }, [tagGroups, library])

  const filtered = useMemo(() => {
    const keyword = query.trim().toLowerCase()
    return library.filter((voice) => {
      if (dimension === 'project') {
        if (projectFilter && (voice.source_display ?? '公共库') !== projectFilter) return false
      } else if (tagFilter.length > 0) {
        // 多选按"任一命中"：这一步是在挑一把能用的声线，不是精确筛资产库。
        const names = voiceTagNames(voice)
        if (!tagFilter.some((name) => names.includes(name))) return false
      }
      if (!keyword) return true
      return `${voice.name} ${voice.source_display ?? ''} ${voiceTagNames(voice).join(' ')}`.toLowerCase().includes(keyword)
    })
  }, [library, query, dimension, projectFilter, tagFilter])

  // 项目清单内禁止重名：两条已选展示名相同即为冲突，必须改名或移除。
  const conflicts = useMemo(() => {
    const seen = new Set<string>()
    const dupes = new Set<string>()
    for (const item of selected) {
      const key = item.displayName.trim()
      if (seen.has(key)) dupes.add(key)
      seen.add(key)
    }
    return dupes
  }, [selected])

  const toggle = (voice: VoiceAsset) => {
    setSelected((current) => {
      const exists = current.find((item) => item.voiceId === voice.id)
      if (exists) return current.filter((item) => item.voiceId !== voice.id)
      return [...current, { voiceId: voice.id, displayName: voice.name }]
    })
  }

  const renameSelected = (voiceId: string, displayName: string) => {
    setSelected((current) => current.map((item) => (item.voiceId === voiceId ? { ...item, displayName } : item)))
  }

  const canCreate = name.trim().length > 0
    && (mode === 'A' || (selected.length > 0 && conflicts.size === 0))
    && !busy

  const create = async () => {
    if (!canCreate) return
    setBusy(true)
    try {
      // 创建与声线挂载都交给组合根：只有它知道"这是新建项目"，落点才能对上。
      const { failed } = await onCreate(name.trim(), mode === 'B' ? selected : [])
      if (failed.length) notify(`项目已创建，但 ${failed.length} 个声线未能加入清单：${failed.join('；')}`)
      onClose()
    } catch (error) { notify((error as Error).message) } finally { setBusy(false) }
  }

  return <div className="dialog-overlay" role="presentation" onClick={onClose}>
    <div className="dialog-card wizard-card" role="dialog" aria-modal="true" aria-label="新建项目" onClick={(event) => event.stopPropagation()}>
      <h3>新建项目 {step === 2 && '· 选择声线'}</h3>
      <div className="dialog-message">
        {step === 1 && <>
          <label className="wizard-field">
            项目名称
            <input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如：有声书第一章" />
          </label>
          <div className="wizard-modes">
            <label><input type="radio" name="wizard-mode" checked={mode === 'A'} onChange={() => setMode('A')} /> 创建新声线（进入步骤 1）</label>
            <label><input type="radio" name="wizard-mode" checked={mode === 'B'} onChange={() => setMode('B')} /> 使用已有声线（进入步骤 2）</label>
          </div>
        </>}

        {step === 2 && <>
          {loadError && <p className="hint">{loadError}</p>}
          <div className="wizard-picker-bar">
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={dimension === 'project' ? '搜索声线名称 / 来源项目…' : '搜索声线名称 / 标签…'}
            />
            {/* 两重区分放在搜索框右边：切换维度只换口径，已勾选的声线一条都不丢。 */}
            <div className="wizard-dims" role="group" aria-label="声线筛选维度">
              <button type="button" className={dimension === 'project' ? 'is-on' : ''} onClick={() => setDimension('project')}>按项目</button>
              <button type="button" className={dimension === 'tag' ? 'is-on' : ''} onClick={() => setDimension('tag')}>按标签</button>
            </div>
          </div>

          {/* 分面条常驻 64px、区内滚动：切维度只换内容，弹窗高度一下都不动。
              空态提示写在容器内部，不在外面另起一行。 */}
          <div className="wizard-facet">
            <button
              type="button"
              className={`tag-option${(dimension === 'project' ? !projectFilter : tagFilter.length === 0) ? ' is-on' : ''}`}
              onClick={() => (dimension === 'project' ? setProjectFilter('') : setTagFilter([]))}
            >
              全部<i className="facet-count">{library.length}</i>
            </button>
            {dimension === 'project'
              ? projectFacets.map((facet) => <button
                key={facet.label}
                type="button"
                className={`tag-option${projectFilter === facet.label ? ' is-on' : ''}`}
                title={facet.label === '公共库' ? '不属于任何项目的声线' : `来源项目：${facet.label}`}
                onClick={() => setProjectFilter(projectFilter === facet.label ? '' : facet.label)}
              >
                {facet.label}<i className="facet-count">{facet.count}</i>
              </button>)
              : tagFacets.map((group) => <span className="wizard-facet-group" key={group.id}>
                <em>{group.name}</em>
                {group.tags.map((tag) => <button
                  key={tag.id}
                  type="button"
                  className={`tag-option${tagFilter.includes(tag.name) ? ' is-on' : ''}`}
                  title={`${group.name} · ${tag.name}（${tag.count} 把声线）`}
                  onClick={() => setTagFilter((current) => current.includes(tag.name)
                    ? current.filter((name) => name !== tag.name)
                    : [...current, tag.name])}
                >
                  {tag.color && <i className="tag-dot" style={{ background: tag.color }} />}
                  {tag.name}<i className="facet-count">{tag.count}</i>
                </button>)}
              </span>)}
            {dimension === 'tag' && tagFacets.length === 0
              && <span className="hint">{tagsLoaded
                ? '还没有声线打过标签：先到「全部声线」页给声线加上标签，这里就能按标签挑。'
                : '正在读取标签…'}</span>}
            {dimension === 'project' && projectFacets.length === 0
              && <span className="hint">{loadError ? '声线库读取失败。' : '声线库还是空的。'}</span>}
          </div>
          {/* 常驻一块高度（空着时给一句说明）：勾选第一条声线时弹窗不再当场长高，
              顺带把"这一区是干什么的"提前讲清楚。
              高度固定为两行胶囊 + 第三行露头，超出在区内滚动；空态单独走 is-empty，
              那一档没有溢出，可以让说明文字在区域正中。 */}
          <div className={selected.length === 0 ? 'wizard-chips is-empty' : 'wizard-chips'}>
            {selected.length === 0
              ? <span className="hint">勾选下方声线后会显示在这里，可就地改名。</span>
              : selected.map((item) => {
                const duplicate = conflicts.has(item.displayName.trim())
                return <span key={item.voiceId} className={duplicate ? 'chip conflict' : 'chip'} title={duplicate ? '项目内声线重名，请改名或移除' : item.displayName}>
                  <input
                    className="chip-name"
                    value={item.displayName}
                    onChange={(event) => renameSelected(item.voiceId, event.target.value)}
                  />
                  <button onClick={() => setSelected((current) => current.filter((each) => each.voiceId !== item.voiceId))}>×</button>
                </span>
              })}
          </div>
          <div className="wizard-voice-list">
            {/* 同一句覆盖两种空：库里真没有，和分面/关键词筛选没命中 */}
            {filtered.length === 0 && <p className="hint">没有符合条件的声线；换个关键词或分面，或取消后先到「上传声线」入库。</p>}
            {filtered.map((voice) => {
              const picked = selected.some((item) => item.voiceId === voice.id)
              // 行内后缀跟着维度走：按项目看来源，按标签看它身上的词条（"区分"在每行上就看得见）。
              const tags = voiceTagNames(voice)
              const meta = dimension === 'project'
                ? voice.source_display ?? '公共库'
                : (tags.slice(0, 2).join(' · ') + (tags.length > 2 ? ` +${tags.length - 2}` : '') || '未打标签')
              return <div key={voice.id} className={picked ? 'wizard-voice picked' : 'wizard-voice'}>
                <label className="wizard-voice-check">
                  <input type="checkbox" checked={picked} onChange={() => toggle(voice)} />
                  <span title={voice.name}>{voice.name} <em className="hint">· {meta}</em></span>
                </label>
                <AudioPlayer url={fileUrl(voice.preview_url)} />
              </div>
            })}
          </div>
        </>}
      </div>
      <div className="dialog-actions">
        <Button onClick={onClose} disabled={busy}>取消</Button>
        {step === 2 && <Button onClick={() => setStep(1)} disabled={busy}>上一步</Button>}
        {step === 1
          // 方式 A 没有第 2 步，这一步就是终点：文案要说清是"创建并进入"，而不是"下一步"。
          ? <Button primary disabled={!name.trim() || busy} onClick={() => (mode === 'A' ? create() : setStep(2))}>
              {mode === 'A' ? (busy ? '创建中…' : '创建并进入第 1 步') : '下一步：选择声线'}
            </Button>
          : <Button primary disabled={!canCreate} onClick={create}>{busy ? '创建中…' : '创建并进入第 2 步'}</Button>}
      </div>
    </div>
  </div>
}
