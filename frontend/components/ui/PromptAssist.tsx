'use client'

import { useEffect, useState } from 'react'
import { composePrompt, PROMPT_DIMENSIONS, VOICE_PROMPT_PRESETS } from '@/lib/promptPresets'
import { Button } from './Button'

/** 声线提示词助手：场景模板一键填入 + 六段式维度组装（弹窗形式）。
 *
 * 只负责产出结果（onApply），是否覆盖已有内容由调用方决定；
 * 维度选择仅存组件内部，关闭弹窗不清空以便继续调整，提示词输入框仍是唯一真值源。
 * onApply 返回是否真正写入，写入成功才收起组装弹窗。
 */
export function PromptAssist({ onApply }: { onApply: (prompt: string, audition: string) => Promise<boolean> }) {
  const [selected, setSelected] = useState<Record<string, string>>({})
  const [open, setOpen] = useState(false)
  // 应用流程（含覆盖确认弹窗）进行中时忽略 Esc 与遮罩点击：
  // 两个弹窗都监听 window keydown，否则一次 Esc 会把确认框和组装弹窗一起关掉。
  const [busy, setBusy] = useState(false)
  const preview = composePrompt(selected)

  const close = () => { if (!busy) setOpen(false) }

  useEffect(() => {
    if (!open || busy) return
    const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false) }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, busy])

  const toggle = (dimKey: string, option: string) => {
    setSelected((current) => {
      const next = { ...current }
      if (next[dimKey] === option) delete next[dimKey]
      else next[dimKey] = option
      return next
    })
  }

  const apply = async () => {
    if (!preview || busy) return
    setBusy(true)
    try {
      if (await onApply(preview, '')) setOpen(false)
    } finally {
      setBusy(false)
    }
  }

  return <div className="prompt-assist">
    <div className="preset-row">
      {VOICE_PROMPT_PRESETS.map((preset) => (
        <Button key={preset.key} small title={preset.prompt} onClick={() => { void onApply(preset.prompt, preset.audition) }}>
          {preset.label}
        </Button>
      ))}
    </div>
    <button className="builder-trigger" onClick={() => setOpen(true)}>按维度组装（每组选一项，自动拼成完整提示词）</button>
    {open && <div className="dialog-overlay" role="presentation" onClick={close}>
      <div className="dialog-card builder-card" role="dialog" aria-modal="true" aria-label="按维度组装提示词" onClick={(event) => event.stopPropagation()}>
        <h3>按维度组装</h3>
        <div className="builder-body">
          {PROMPT_DIMENSIONS.map((dim) => (
            <div key={dim.key} className="dim-group">
              <span className="dim-label">{dim.label}</span>
              <div className="dim-options">
                {dim.options.map((option) => (
                  <Button key={option} small selected={selected[dim.key] === option} onClick={() => toggle(dim.key, option)}>
                    {option}
                  </Button>
                ))}
              </div>
            </div>
          ))}
        </div>
        <p className="prompt-preview">{preview || '从上面每组各选一项，这里会实时拼出完整提示词'}</p>
        <div className="dialog-actions">
          <Button onClick={close} disabled={busy}>关闭</Button>
          <Button primary disabled={!preview || busy} onClick={() => { void apply() }}>{busy ? '应用中…' : '应用到提示词'}</Button>
        </div>
      </div>
    </div>}
  </div>
}
