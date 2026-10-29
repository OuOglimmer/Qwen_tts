'use client'

import type { Notify } from '@/lib/types'
import { SectionTitle } from '@/components/ui'
import { CloneTool } from './CloneTool'
import { ConvertTool } from './ConvertTool'
import { DesignTool } from './DesignTool'

const META = {
  design: {
    title: 'VoiceDesign 独立试听',
    description: '用自然语言描述直接生成一段试听，不进入项目流程。正式创建声线请使用「声线」工作台。',
  },
  clone: {
    title: 'VoiceClone 独立克隆',
    description: '上传参考音频直接克隆一段试听，不保存资产。正式归档请使用「上传声线」。',
  },
  convert: {
    title: '音频转换',
    description: '在 WAV（24kHz 单声道）与 128kbps MP3 之间互相转换。',
  },
} as const

export type ToolType = keyof typeof META

/** 工具页外壳：只负责标题与分发，三个工具各自独立实现。 */
export default function ToolPage({ type, notify }: { type: ToolType; notify: Notify }) {
  const meta = META[type]
  return <>
    <SectionTitle title={meta.title} description={meta.description} />
    <div className="panel-grid tool-layout">
      {type === 'design' && <DesignTool notify={notify} />}
      {type === 'clone' && <CloneTool notify={notify} />}
      {type === 'convert' && <ConvertTool notify={notify} />}
    </div>
  </>
}
