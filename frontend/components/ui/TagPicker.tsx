'use client'

import { useState } from 'react'
import type { TagGroup } from '@/lib/types'
import { Button } from './Button'
import { Select } from './Select'

/** 按「组」渲染的标签选择器。
 *
 *  为什么不是一串自由输入：单选组（角色定位、制作状态、优先级）如果放任用户敲字，
 *  很快会出现"温柔 / 温柔女声 / 温柔的"三种写法，筛选当场失效。所以这里由组决定行为 ——
 *  ``multi=0`` 点一个自动换掉同组另一个，``multi=1`` 可多选。
 *
 *  高度是稳定的：组的数量由标签域固定，词条换行不改变总高度，弹窗不会因此跳动。
 */
export function TagPicker({ groups, value, onChange, onCreate, hint }: {
  groups: TagGroup[]
  value: string[]
  onChange: (ids: string[]) => void
  /** 新建词条：返回新标签 id（父组件负责调接口与刷新）。 */
  onCreate?: (groupId: string, name: string) => Promise<string | null>
  hint?: string
}) {
  const [targetGroup, setTargetGroup] = useState('')
  const [draft, setDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const selected = new Set(value)
  const activeGroups = groups.filter((group) => group.tags.length > 0 || group.multi === 1)
  const creatable = groups.filter((group) => group.multi === 1 || group.tags.length === 0)
  // 新建只能落进"多选组"或"空组"：单选组（性别年龄）塞第二个词条会自相矛盾。
  // 默认值要跟着 creatable 走，不能取 groups[0]——那多半是个单选组，Select 收到
  // 不在 options 里的值就只剩一个空框（看起来像"下拉坏了"）。
  const createTarget = creatable.some((group) => group.id === targetGroup) ? targetGroup : creatable[0]?.id ?? ''

  const toggle = (group: TagGroup, tagId: string, on: boolean) => {
    if (group.multi === 0) {
      // 单选组：点新的就换掉同组旧的；点已选中的就取消。
      const others = group.tags.filter((tag) => tag.id !== tagId).map((tag) => tag.id)
      const rest = value.filter((id) => !others.includes(id))
      onChange(on ? value.filter((id) => id !== tagId) : [...rest, tagId])
      return
    }
    onChange(on ? value.filter((id) => id !== tagId) : [...value, tagId])
  }

  const create = async () => {
    const name = draft.trim()
    if (!name || !onCreate || !createTarget) return
    setBusy(true)
    try {
      const created = await onCreate(createTarget, name)
      if (created) onChange([...value, created])
      setDraft('')
    } finally {
      setBusy(false)
    }
  }

  return <div className="tag-picker">
    {activeGroups.map((group) => <div className="tag-group" key={group.id}>
      <span className="tag-group-name">
        {group.name}
        <em>{group.multi === 0 ? '单选' : '多选'}</em>
      </span>
      <div className="tag-group-options">
        {group.tags.length === 0 && <span className="hint">这一组还没有词条，在下面新建</span>}
        {group.tags.map((tag) => {
          const on = selected.has(tag.id)
          return <button
            type="button"
            key={tag.id}
            className={`tag-option${on ? ' is-on' : ''}`}
            title={`${group.name} · ${tag.name}`}
            onClick={() => toggle(group, tag.id, on)}
          >
            {tag.color && <i className="tag-dot" style={{ background: tag.color }} />}
            {tag.name}
          </button>
        })}
      </div>
    </div>)}

    {onCreate && <div className="tag-create">
      <Select
        ariaLabel="新建标签所属组"
        value={createTarget}
        onChange={setTargetGroup}
        options={creatable.map((group) => ({ value: group.id, label: group.name }))}
      />
      <input
        value={draft}
        placeholder="新标签名（回车新建）"
        onChange={(event) => setDraft(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === 'Enter') { event.preventDefault(); void create() }
        }}
      />
      <Button small disabled={!draft.trim() || busy} onClick={() => void create()}>新建</Button>
    </div>}
    {hint && <p className="hint">{hint}</p>}
  </div>
}
