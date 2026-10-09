import type { Metadata } from 'next'
import './globals.css'
import './theme.css'

export const metadata: Metadata = {
  title: '声线工作台 · Qwen TTS',
  description: 'Qwen TTS Voiceover Studio',
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="zh-CN"><body>{children}</body></html>
}
