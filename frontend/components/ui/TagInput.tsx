'use client'

import { useState } from 'react'
import type { Tag, TagGroup } from '@/lib/types'

/** 标签输入：回车 / 逗号 / 失焦提交一个标签，点 × 删除，退格删最后一个。
 *  对外值仍是逗号分隔串，与 voices.tags 的落库格式一致；真正的关系表由后端解析。
 *
 *  传了 ``groups`` 时建议区按组渲染、带上颜色点：单选组（性别年龄）点新标签会
 *  自动换掉同组旧标签，避免出现"男声, 女声"这种自相矛盾的组合。 */
export function TagInput({ value, onChange, suggestions = [], groups, placeholder = '输入标签后回车' }: {
  value: string
  onChange: (value: string) => void
  /** 库里已有的标签，点一下就加，避免同义标签各写一遍。 */
  suggestions?: string[]
  /** 标签组（含词条与单选/多选标记）；给了就按组渲染建议区。 */
  groups?: TagGroup[]
  placeholder?: string
}) {
  const [draft, setDraft] = useState('')
  const tags = value.split(',').map((tag) => tag.trim()).filter(Boolean)

  /** 名称 → (组, 词条)：单选组要能回溯到"该替换谁"。 */
  const lookup = new Map<string, { group: TagGroup; tag: Tag }>()
  for (const group of groups ?? []) {
    for (const tag of group.tags) lookup.set(tag.name, { group, tag })
  }

  const commit = (raw: string) => {
    const additions = raw.split(',').map((tag) => tag.trim()).filter(Boolean)
    if (additions.length === 0) return
    const next = [...tags]
    for (const tag of additions) {
      const hit = lookup.get(tag)
      if (hit && hit.group.multi === 0) {
        // 单选组：先踢掉同组其它词条，再追加自己。
        const siblings = new Set(hit.group.tags.map((item) => item.name).filter((name) => name !== tag))
        for (let index = next.length - 1; index >= 0; index -= 1) {
          if (siblings.has(next[index])) next.splice(index, 1)
        }
      }
      if (!next.includes(tag)) next.push(tag)
    }
    onChange(next.join(','))
    setDraft('')
  }

  const remove = (tag: string) => onChange(tags.filter((item) => item !== tag).join(','))

  const unusedPlain = suggestions.filter((tag) => !tags.includes(tag)).slice(0, 6)
  const unusedGroups = (groups ?? [])
    .map((group) => ({ group, tags: group.tags.filter((tag) => !tags.includes(tag.name)) }))
    .filter((entry) => entry.tags.length > 0)

  return (
    <div className="tag-input">
      <div className="tag-input-box">
        {tags.map((tag) => (
          <span className="tag-chip" key={tag}>
            {/* 文字单独包一层：省略号只对"可收缩的子项"生效，直接放匿名文本节点上不起作用 */}
            <span className="tag-chip-text" title={tag}>{tag}</span>
            <button type="button" className="tag-chip-x" aria-label={`删除标签 ${tag}`} title={`删除标签 ${tag}`} onClick={() => remove(tag)}>×</button>
          </span>
        ))}
        <input
          className="tag-input-field"
          value={draft}
          placeholder={tags.length === 0 ? placeholder : ''}
          onChange={(event) => {
            const next = event.target.value
            // 含逗号即视为一次提交，便于从别处整段粘贴标签。
            if (next.includes(',')) commit(next)
            else setDraft(next)
          }}
          onKeyDown={(event) => {
            if (event.key === 'Enter') { event.preventDefault(); commit(draft) }
            else if (event.key === 'Backspace' && !draft && tags.length > 0) remove(tags[tags.length - 1])
          }}
        />
      </div>
      {/* 始终占位：建议来自库里的标签，是异步拉回来的，从无到有会把它下面的字段整体下推。
          空着的时候只是留出这一行的高度，不渲染按钮。 */}
      <div className="tag-suggest" aria-hidden={unusedPlain.length === 0 && unusedGroups.length === 0}>
        {unusedGroups.length === 0 && unusedPlain.map((tag) => (
          <button type="button" className="tag-suggest-btn" key={tag} onClick={() => commit(tag)}>＋ {tag}</button>
        ))}
        {unusedGroups.map(({ group, tags: rest }) => <span className="tag-suggest-group" key={group.id}>
          <em>{group.name}</em>
          {rest.slice(0, 6).map((tag) => <button
            type="button"
            className="tag-suggest-btn"
            key={tag.id}
            title={`${group.name} · ${tag.name}${group.multi === 0 ? '（单选组，会替换同组标签）' : ''}`}
            onClick={() => commit(tag.name)}
          >
            {tag.color && <i className="tag-dot" style={{ background: tag.color }} />}＋ {tag.name}
          </button>)}
        </span>)}
      </div>
    </div>
  )
}
