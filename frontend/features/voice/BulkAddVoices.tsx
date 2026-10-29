'use client'

import { useEffect, useMemo, useState } from 'react'
import { Button, Label, LanguageSelect } from '@/components/ui'

/** 一次最多新增的槽位数：后端硬上限 50 且会静默截断多余的名单，这里收紧到可一次看完的量级。 */
const MAX_NEW_SLOTS = 20
const NAME_MAX_LENGTH = 80
const DEFAULT_PREFIX = '声线'

type Row = { name: string; edited: boolean }

const escapeRegExp = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

/** 接续项目里已有的「前缀+数字」编号生成 size 个新名字，自动跳过已占用的编号。 */
function nextNames(prefix: string, size: number, existing: string[]): string[] {
  const cleanPrefix = prefix.trim() || DEFAULT_PREFIX
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

/** 「添加多条声线」弹层：条数与名单实时同步，改过名的行保留，其余随前缀重新编号。 */
export function BulkAddVoices({ existingNames, language: initialLanguage, defaultCount = 4, onCancel, onSubmit }: {
  existingNames: string[]
  language: string
  /** 打开时的默认条数：唯一入口是左侧「添加多条声线」，4 条起步。 */
  defaultCount?: number
  onCancel: () => void
  onSubmit: (names: string[], language: string) => Promise<void>
}) {
  const [prefix, setPrefix] = useState(DEFAULT_PREFIX)
  const [rows, setRows] = useState<Row[]>(() => nextNames(DEFAULT_PREFIX, Math.min(MAX_NEW_SLOTS, Math.max(1, defaultCount)), existingNames).map((name) => ({ name, edited: false })))
  const [language, setLanguage] = useState(initialLanguage)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onCancel])

  const names = useMemo(() => rows.map((row) => row.name), [rows])

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

  /** 条数即名单行数：增多按前缀续编号补默认名，减少从末尾裁掉。 */
  const resizeTo = (target: number) => {
    const size = Math.min(MAX_NEW_SLOTS, Math.max(1, Math.trunc(target) || 1))
    setRows((current) => {
      if (size === current.length) return current
      if (size < current.length) return current.slice(0, size)
      const taken = [...existingNames, ...current.map((row) => row.name)]
      const appended = nextNames(prefix, size - current.length, taken).map((name) => ({ name, edited: false }))
      return [...current, ...appended]
    })
  }

  /** 换前缀只重排未改名的行，避免覆盖用户已经想好的名字。 */
  const changePrefix = (value: string) => {
    setPrefix(value)
    const clean = value.trim() || DEFAULT_PREFIX
    setRows((current) => {
      const editedNames = current.filter((row) => row.edited).map((row) => row.name)
      const generated = nextNames(clean, current.length - editedNames.length, [...existingNames, ...editedNames])
      let cursor = 0
      return current.map((row) => (row.edited ? row : { name: generated[cursor++], edited: false }))
    })
  }

  const renameAt = (index: number, value: string) => setRows((current) => current.map((row, position) => (position === index ? { name: value, edited: true } : row)))
  const removeAt = (index: number) => setRows((current) => current.filter((_, position) => position !== index))

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
          <input value={prefix} onChange={(event) => changePrefix(event.target.value)} placeholder={DEFAULT_PREFIX} />
        </div>
        <div>
          <Label>条数</Label>
          <input type="number" min={1} max={MAX_NEW_SLOTS} value={rows.length} onChange={(event) => resizeTo(Number(event.target.value))} />
        </div>
        <div>
          <Label>语言</Label>
          <LanguageSelect value={language} onChange={setLanguage} />
        </div>
      </div>
      <p className="hint">条数与下面的名单实时同步：加条数在末尾补默认名，减条数从末尾删行；改前缀只重排没改过名的行。名字暂时不定就保留默认名，之后在工作表里随时改。新槽位以「草稿」状态进入工作表，不影响当前表单里正在编辑的声线。</p>
      <div className="bulk-names">
        {rows.map((row, index) => <div key={index} className="bulk-name-row">
          <div className="bulk-name-line">
            <span className="bulk-name-index">{index + 1}</span>
            <input value={row.name} onChange={(event) => renameAt(index, event.target.value)} />
            <Button small disabled={rows.length === 1} title="删除这一行" onClick={() => removeAt(index)}>✕</Button>
          </div>
          {problems[index] && <span className="bulk-problem">{problems[index]}</span>}
        </div>)}
      </div>
      <div className="bulk-actions">
        <Button small disabled={rows.length >= MAX_NEW_SLOTS} onClick={() => resizeTo(rows.length + 1)}>＋ 再加一条</Button>
        <span className="hint">共 {rows.length} 条{rows.length >= MAX_NEW_SLOTS ? `（已达单次上限 ${MAX_NEW_SLOTS}）` : ''}</span>
      </div>
      <div className="dialog-actions">
        <Button onClick={onCancel}>取消</Button>
        <Button primary disabled={invalid || submitting || rows.length === 0} onClick={submit}>{submitting ? '添加中…' : `确认添加 ${rows.length} 条`}</Button>
      </div>
    </div>
  </div>
}
