import type { Metadata } from 'next'
import './globals.css'
import './theme.css'

export const metadata: Metadata = {
  title: '声线工作台 · Qwen TTS',
  description: 'Qwen TTS Voiceover Studio',
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      {/* 首屏前定主题，避免暗色下白闪：读 localStorage，缺省明亮 */}
      <head>
        <script dangerouslySetInnerHTML={{ __html: `(function(){try{var t=localStorage.getItem('theme');if(t!=='dark'&&t!=='light')t='light';document.documentElement.dataset.theme=t;}catch(e){document.documentElement.dataset.theme='light';}})()` }} />
      </head>
      <body>{children}</body>
    </html>
  )
}
