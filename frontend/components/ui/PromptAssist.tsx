'use client'

import { useState } from 'react'
import { composePrompt, PROMPT_DIMENSIONS, VOICE_PROMPT_PRESETS } from '@/lib/promptPresets'
import { Button } from './Button'

/** 声线提示词助手：场景模板一键填入 + 六段式维度组装。
 *
 * 只负责产出结果（onApply），是否覆盖已有内容、试听文本何时填入由调用方决定；
 * 维度选择仅存组件内部，提示词输入框仍是唯一真值源。
 */
export function PromptAssist({ onApply }: { onApply: (prompt: string, audition: string) => void }) {
  const [selected, setSelected] = useState<Record<string, string>>({})
  const preview = composePrompt(selected)

  const toggle = (dimKey: string, option: string) => {
    setSelected((current) => {
      const next = { ...current }
      if (next[dimKey] === option) delete next[dimKey]
      else next[dimKey] = option
      return next
    })
  }

  return <div className="prompt-assist">
    <div className="preset-row">
      {VOICE_PROMPT_PRESETS.map((preset) => (
        <Button key={preset.key} small title={preset.prompt} onClick={() => onApply(preset.prompt, preset.audition)}>
          {preset.label}
        </Button>
      ))}
    </div>
    <details className="prompt-builder">
      <summary>按维度组装（每组选一项，自动拼成完整提示词）</summary>
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
      <p className="prompt-preview">{preview || '从上面每组各选一项，这里会实时拼出完整提示词'}</p>
      <Button small primary disabled={!preview} onClick={() => onApply(preview, '')}>应用到提示词</Button>
    </details>
  </div>
}
