import type { VoiceAsset } from '@/lib/types'

/** 已固化声线下拉框：全站同一份 option 列表（名称 · 来源），空态文案可定制。
 *  placeholderAlways：即使已有声线也保留一个空选项，供"必须主动选择"的场景。 */
export function VoiceSelect({ voices, value, onChange, placeholder = '请选择', placeholderAlways = false, className }: {
  voices: VoiceAsset[]
  value: string
  onChange: (value: string) => void
  placeholder?: string
  placeholderAlways?: boolean
  className?: string
}) {
  return (
    <select value={value} onChange={(event) => onChange(event.target.value)} className={className}>
      {(placeholderAlways || voices.length === 0) && <option value="">{placeholder}</option>}
      {voices.map((voice) => <option key={voice.id} value={voice.id}>{voice.name} · {voice.source}</option>)}
    </select>
  )
}
