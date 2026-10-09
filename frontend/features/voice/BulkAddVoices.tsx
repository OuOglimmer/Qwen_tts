'use client'

import { useEffect, useMemo, useState } from 'react'
import { Button, Label, LanguageSelect } from '@/components/ui'

/** 一次最多新增的槽位数：后端硬上限 50 且会静默截断多余的名单，这里收紧到可一次看完的量级。 */
const MAX_NEW_SLOTS = 20
const NAME_MAX_LENGTH = 80

const escapeRegExp = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/** 接续项目里已有的「前缀+数字」编号生成 size 个新名字，自动跳过已占用的编号。 */
function nextNames(prefix: string, size: number, existing: string[]): string[] {
  const cleanPrefix = prefix.trim() || '声线'
  const pattern = new RegExp(`^${escapeRegExp(cleanPrefix)}(\\d+)$`)
  const used = new Set(existing.map((name) => name.trim()).filter(Boolean))
  let highest = 0
  used.forEach((name) => {
    const matched = pattern.exec(name)
    if (matched) highest = Math.max(highest, Number(matched[1]))
  })
  const created: string[] = []
  let cursor = highest
  while (created.length < size) {
    cursor += 1
    const candidate = `${cleanPrefix}${cursor}`
    if (used.has(candidate)) continue
    used.add(candidate)
    created.push(candidate)
  }
  return created
}

/** 「添加多条声线」弹层：定条数 → 罗列默认名 → 逐行改名 → 一次性建草稿槽位。 */
export function BulkAddVoices({ existingNames, language: initialLanguage, onCancel, onSubmit }: {
  existingNames: string[]
  language: string
  onCancel: () => void
  onSubmit: (names: string[], language: string) => Promise<void>
}) {
  const [prefix, setPrefix] = useState('声线')
  const [count, setCount] = useState(4)
  const [names, setNames] = useState<string[]>(() => nextNames('声线', 4, existingNames))
  const [language, setLanguage] = useState(initialLanguage)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel])

  const problems = useMemo(() => {
    const trimmed = names.map((name) => name.trim())
    const projectNames = new Set(existingNames)
    const occurrences = new Map<string, number>()
    trimmed.forEach((name) => occurrences.set(name, (occurrences.get(name) ?? 0) + 1))
    return trimmed.map((name) => {
      if (!name) return '名称不能为空'
      if (name.length > NAME_MAX_LENGTH) return `名称最多 ${NAME_MAX_LENGTH} 个字符`
      if (projectNames.has(name)) return '项目里已有同名声线'
      if ((occurrences.get(name) ?? 0) > 1) return '与本清单其他行重名'
      return ''
    })
  }, [names, existingNames])

  const renameAt = (index: number, value: string) => setNames((current) => current.map((name, position) => (position === index ? value : name)))
  const removeAt = (index: number) => setNames((current) => current.filter((_, position) => position !== index))
  const regenerate = () => setNames(nextNames(prefix, Math.min(MAX_NEW_SLOTS, Math.max(1, Math.trunc(count) || 1)), existingNames))
  const appendOne = () => setNames((current) => [...current, ...nextNames(prefix, 1, [...existingNames, ...current])])

  const invalid = problems.some(Boolean)
  const submit = async () => {
    if (invalid || submitting) return
    setSubmitting(true)
    try {
      await onSubmit(names.map((name) => name.trim()), language)
    } finally {
      setSubmitting(false)
    }
  }

  return <div className="dialog-overlay" role="presentation" onClick={onCancel}>
    <div className="dialog-card bulk-card" role="dialog" aria-modal="true" aria-label="添加多条声线" onClick={(event) => event.stopPropagation()}>
      <h3>添加多条声线</h3>
      <div className="bulk-setup">
        <div>
          <Label>前缀</Label>
          <input value={prefix} onChange={(event) => setPrefix(event.target.value)} placeholder="声线" />
        </div>
        <div>
          <Label>条数</Label>
          <input type="number" min={1} max={MAX_NEW_SLOTS} value={count} onChange={(event) => setCount(Number(event.target.value))} />
        </div>
        <div>
          <Label>语言</Label>
          <LanguageSelect value={language} onChange={setLanguage} />
        </div>
        <Button small onClick={regenerate}>按条数生成名单</Button>
      </div>
      <p className="hint">生成后可逐行改名；名字暂时不定就保留默认名，之后在工作表里随时改。新槽位以「草稿」状态进入工作表，不影响当前表单里正在编辑的声线。</p>
      <div className="bulk-names">
        {names.map((name, index) => <div key={index} className="bulk-name-row">
          <div className="bulk-name-line">
            <span className="bulk-name-index">{index + 1}</span>
            <input value={name} onChange={(event) => renameAt(index, event.target.value)} />
            <Button small disabled={names.length === 1} title="删除这一行" onClick={() => removeAt(index)}>✕</Button>
          </div>
          {problems[index] && <span className="bulk-problem">{problems[index]}</span>}
        </div>)}
      </div>
      <div className="bulk-actions">
        <Button small disabled={names.length >= MAX_NEW_SLOTS} onClick={appendOne}>＋ 再加一条</Button>
        <span className="hint">共 {names.length} 条{names.length >= MAX_NEW_SLOTS ? `（已达单次上限 ${MAX_NEW_SLOTS}）` : ''}</span>
      </div>
      <div className="dialog-actions">
        <Button onClick={onCancel}>取消</Button>
        <Button primary disabled={invalid || submitting || names.length === 0} onClick={submit}>{submitting ? '添加中…' : `确认添加 ${names.length} 条`}</Button>
      </div>
    </div>
  </div>
}
