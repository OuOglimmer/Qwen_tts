import { LANGUAGES } from '@/lib/constants'

export function LanguageSelect({ value, onChange }: { value: string; onChange: (value: string) => void }) {
  return (
    <select value={value} onChange={(event) => onChange(event.target.value)}>
      {LANGUAGES.map((item) => <option key={item}>{item}</option>)}
    </select>
  )
}
