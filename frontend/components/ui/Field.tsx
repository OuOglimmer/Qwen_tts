import type { ReactNode } from 'react'

export function Label({ children }: { children: ReactNode }) {
  return <span className="field-label">{children}</span>
}
